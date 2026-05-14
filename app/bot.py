"""Telegram bot: voice-message → pipeline → review URL DM.

Commands:
  /start    — friendly intro
  /list     — last 10 captures + statuses
  /retry id — re-run the pipeline on a failed capture
  /delete id — soft-delete a capture (excluded from card export)
  /export   — DM the latest incremental .apkg

Runs via webhook (Telegram POSTs to /tg/webhook/<secret>). The voice handler runs
the full pipeline inline so the request stays alive on Cloud Run; idempotency on
the Telegram update_id prevents duplicate work on webhook retries.
"""

from __future__ import annotations

import logging
import secrets as secrets_mod
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from app import db, storage

from app import cards as cards_module
from app import pipeline as pipe
from app.config import settings
from app.models import Capture, CaptureStatus

logger = logging.getLogger(__name__)


def review_url(capture_id: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/r/{capture_id}"


def new_capture_id() -> str:
    return secrets_mod.token_hex(16)


def get_bot() -> Bot:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(bot: Bot) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(msg: Message) -> None:
        await msg.answer(
            "Send me a voice message <i>or</i> a text message with an English phrase "
            f"you want to learn in {settings.target_lang_name}. I'll translate it, "
            "generate audio for shadowing, and build vocab cards for Anki.\n\n"
            "Commands: /list /retry &lt;id&gt; /delete &lt;id&gt; /export"
        )

    @dp.message(Command("list"))
    async def cmd_list(msg: Message) -> None:
        rows = await db.recent_visible(limit=10)
        if not rows:
            await msg.answer("No captures yet. Send a voice message.")
            return
        lines = ["<b>Recent:</b>"]
        for c in rows:
            preview = (c.it_text or c.en_transcript or "(pending)")[:60]
            lines.append(
                f"<code>{c.id[:8]}</code> [{c.status}] {preview}"
            )
        await msg.answer("\n".join(lines))

    @dp.message(Command("retry"))
    async def cmd_retry(msg: Message) -> None:
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Usage: /retry &lt;capture_id&gt;")
            return
        cid_prefix = parts[1].strip()
        rows = await db.find_by_id_prefix(cid_prefix)
        if not rows:
            await msg.answer("Not found.")
            return
        if len(rows) > 1:
            await msg.answer(f"Ambiguous prefix — {len(rows)} matches. Be more specific.")
            return
        cap = rows[0]
        await msg.answer(f"Retrying <code>{cap.id[:8]}</code>…")
        await _run_and_notify(bot, msg.chat.id, cap.id)

    @dp.message(Command("delete"))
    async def cmd_delete(msg: Message) -> None:
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Usage: /delete &lt;capture_id&gt;")
            return
        cid_prefix = parts[1].strip()
        rows = await db.find_by_id_prefix(cid_prefix)
        if not rows:
            await msg.answer("Not found.")
            return
        if len(rows) > 1:
            await msg.answer(f"Ambiguous prefix — {len(rows)} matches.")
            return
        cap = rows[0]
        await db.soft_delete(cap.id, datetime.utcnow())
        await msg.answer(f"Deleted <code>{cap.id[:8]}</code>.")

    @dp.message(Command("export"))
    async def cmd_export(msg: Message) -> None:
        rows = await db.ready_pending_export()
        if not rows:
            await msg.answer("Nothing new to export.")
            return
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        out = cards_module.output_path(f"italian-captured-{ts}-incremental.apkg")
        await cards_module.build_package_async(rows, out)
        await db.mark_exported([r.id for r in rows], datetime.utcnow())
        await _send_file(bot, msg.chat.id, out, caption=f"{len(rows)} new captures.")

    @dp.message(F.voice | F.audio)
    async def on_voice(msg: Message) -> None:
        update_id = getattr(msg, "message_id", None)
        # Idempotency: if this Telegram update was already processed (webhook retry),
        # silently no-op.
        if update_id is not None:
            existing = await db.find_by_update_id(update_id)
            if existing is not None:
                logger.info("idempotent skip — update_id %s already processed", update_id)
                return

        capture_id = new_capture_id()
        # Voice memos are downloaded to /tmp on Cloud Run; the file is consumed by
        # the transcribe stage in the same request and not needed after.
        audio_dir = settings.audio_dir
        try:
            audio_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            audio_dir = Path("/tmp")
        local_path = audio_dir / f"{capture_id}.ogg"

        file_id = msg.voice.file_id if msg.voice else msg.audio.file_id  # type: ignore[union-attr]
        tg_file = await bot.get_file(file_id)
        if tg_file.file_path is None:
            await msg.answer("Couldn't fetch the voice message file. Try again.")
            return
        await bot.download_file(tg_file.file_path, destination=local_path)

        cap = Capture(
            id=capture_id,
            telegram_user_id=msg.from_user.id if msg.from_user else None,
            telegram_chat_id=msg.chat.id,
            telegram_update_id=update_id,
            en_audio_path=str(local_path),
            status=CaptureStatus.pending,
        )
        try:
            await db.create(cap)
        except Exception:
            # Concurrent retry inserted the same id before us — bail.
            logger.info("idempotent race — capture %s already inserted", capture_id)
            return

        await msg.answer(
            f"Got it — processing <code>{capture_id[:8]}</code>…"
        )
        # Run pipeline inline so the webhook request stays alive on Cloud Run
        # (background tasks aren't reliable after the response in request-driven mode).
        await _run_and_notify(bot, msg.chat.id, capture_id)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def on_text(msg: Message) -> None:
        text = (msg.text or "").strip()
        if not text:
            return

        update_id = getattr(msg, "message_id", None)
        if update_id is not None:
            existing = await db.find_by_update_id(update_id)
            if existing is not None:
                logger.info("idempotent skip — update_id %s already processed", update_id)
                return

        capture_id = new_capture_id()
        cap = Capture(
            id=capture_id,
            telegram_user_id=msg.from_user.id if msg.from_user else None,
            telegram_chat_id=msg.chat.id,
            telegram_update_id=update_id,
            en_transcript=text,
            status=CaptureStatus.pending,
        )
        try:
            await db.create(cap)
        except Exception:
            logger.info("idempotent race — capture %s already inserted", capture_id)
            return

        await msg.answer(f"Got it — processing <code>{capture_id[:8]}</code>…")
        await _run_and_notify(bot, msg.chat.id, capture_id)

    return dp


async def _run_and_notify(bot: Bot, chat_id: int, capture_id: str) -> None:
    async def on_ready(cap: Capture) -> None:
        url = review_url(cap.id)
        n_cards = len(cap.cards or [])
        suffix = f" · {n_cards} card{'s' if n_cards != 1 else ''}" if n_cards else ""
        await bot.send_message(
            chat_id,
            f"<b>{cap.it_text}</b>\n"
            f"<i>{cap.en_transcript}</i>\n\n"
            f"<a href=\"{url}\">Open review</a>{suffix}",
            disable_web_page_preview=True,
        )
        if cap.it_audio_path:
            try:
                audio_bytes = await storage.fetch_audio_bytes(cap.it_audio_path)
                await bot.send_voice(
                    chat_id,
                    voice=BufferedInputFile(audio_bytes, filename=f"{cap.id}.mp3"),
                )
            except Exception:
                logger.exception("failed to send audio for %s", cap.id)

    async def on_failure(cap: Capture) -> None:
        await bot.send_message(
            chat_id,
            f"Capture <code>{cap.id[:8]}</code> failed: {cap.error or 'unknown'}\n"
            f"Send <code>/retry {cap.id[:8]}</code> to retry.",
        )

    await pipe.run_pipeline(capture_id, on_ready=on_ready, on_failure=on_failure)


async def _send_file(bot: Bot, chat_id: int, path: Path, caption: str = "") -> None:
    await bot.send_document(
        chat_id,
        document=BufferedInputFile(path.read_bytes(), filename=path.name),
        caption=caption or None,
    )
