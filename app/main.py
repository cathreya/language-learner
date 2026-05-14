"""FastAPI entrypoint + Telegram webhook handler.

The bot runs via Telegram webhooks (Telegram POSTs incoming updates to
/tg/webhook/<secret>). This lets the app scale to zero on Cloud Run — there is
no long-running poller keeping the container awake.
"""

from __future__ import annotations

import logging
import secrets as secrets_mod
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from app import db, pipeline
from app.bot import build_dispatcher, get_bot
from app.config import settings
from app.web import router

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("language-learner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    # Reset captures that died mid-pipeline on a prior container so the user can retry.
    try:
        n = await pipeline.recovery_sweep(stale_minutes=5)
        if n:
            logger.info("recovery sweep: %d captures reset to failed", n)
    except Exception:
        logger.exception("recovery sweep failed at startup (non-fatal)")
    if settings.telegram_bot_token:
        app.state.bot = get_bot()
        app.state.dispatcher = build_dispatcher(app.state.bot)
        logger.info("telegram bot ready (webhook mode)")
    else:
        app.state.bot = None
        app.state.dispatcher = None
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled")
    try:
        yield
    finally:
        if app.state.bot is not None:
            await app.state.bot.session.close()


app = FastAPI(title="language-learner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.get("/health")
async def health() -> dict:
    # NOTE: must not be /healthz — Cloud Run's Google Frontend reserves that path
    # and intercepts it with a 404 before requests reach the container.
    return {"ok": True}


@app.post("/tg/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request) -> dict:
    """Telegram POSTs incoming updates here.

    - `secret` path segment is the webhook secret (also validated against the
      Telegram-set X-Telegram-Bot-Api-Secret-Token header if configured).
    - Returns 200 only AFTER the pipeline completes so Telegram knows we got it.
      Pipeline runs inline in this handler (5-15s typical). Idempotency on
      `update_id` guards against retries.
    """
    if not settings.telegram_webhook_secret:
        raise HTTPException(503, "webhook secret not configured")
    if not secrets_mod.compare_digest(secret, settings.telegram_webhook_secret):
        raise HTTPException(403, "bad secret")

    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if header_secret and not secrets_mod.compare_digest(
        header_secret, settings.telegram_webhook_secret
    ):
        raise HTTPException(403, "bad header secret")

    if app.state.bot is None or app.state.dispatcher is None:
        raise HTTPException(503, "bot not configured")

    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(400, f"bad json: {e}") from e

    update = Update.model_validate(payload, context={"bot": app.state.bot})
    await app.state.dispatcher.feed_update(app.state.bot, update)
    return {"ok": True}
