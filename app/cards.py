"""Generate Anki .apkg files from captures."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterable
from pathlib import Path

import genanki

from app import storage
from app.config import settings
from app.models import Capture

# Stable IDs so repeated imports merge into the same deck/model.
DECK_ID = 1736700101
MODEL_ID = 1736700102

MODEL = genanki.Model(
    MODEL_ID,
    "Captured-Italian",
    fields=[
        {"name": "Front"},
        {"name": "Back"},
        {"name": "Lemma"},
        {"name": "POS"},
        {"name": "ExampleIT"},
        {"name": "ExampleEN"},
        {"name": "Audio"},
        {"name": "CaptureID"},
    ],
    templates=[
        {
            "name": "EN→IT",
            "qfmt": (
                '<div style="font-size:22px;">{{Front}}</div>'
                '<div style="margin-top:18px;color:#555;font-size:14px;">'
                "<i>{{ExampleEN}}</i></div>"
            ),
            "afmt": (
                "{{FrontSide}}"
                '<hr id="answer">'
                '<div style="font-size:28px;font-family:Georgia,serif;">{{Back}}</div>'
                '<div style="margin-top:8px;color:#777;">{{Lemma}} <span style="font-size:12px;'
                'text-transform:uppercase;letter-spacing:0.05em;">{{POS}}</span></div>'
                '<div style="margin-top:18px;font-family:Georgia,serif;">{{ExampleIT}}</div>'
                '<div style="margin-top:8px;">{{Audio}}</div>'
            ),
        }
    ],
    css=(
        ".card { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; "
        "color: #1a1a1a; background: #fbfaf7; padding: 16px; }"
    ),
)


async def build_package_async(captures: Iterable[Capture], out_path: Path) -> Path:
    """Build a .apkg, downloading any GCS-stored audio to a temp dir first.

    Audio is embedded as media; Anki references it via [sound:filename.mp3].
    """
    deck = genanki.Deck(DECK_ID, "Italian — captured")
    media_files: list[str] = []
    seen_media: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="anki-media-") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        for cap in captures:
            cards = cap.cards or []
            audio_tag = ""
            if cap.it_audio_path:
                local_audio = tmpdir / f"{cap.id}.mp3"
                try:
                    await storage.download_to_path(cap.it_audio_path, local_audio)
                except Exception:  # noqa: BLE001
                    local_audio = None  # type: ignore[assignment]
                if local_audio and local_audio.exists():
                    key = str(local_audio)
                    if key not in seen_media:
                        media_files.append(key)
                        seen_media.add(key)
                    audio_tag = f"[sound:{local_audio.name}]"

            for c in cards:
                # Anki .apkg only emits the EN→target direction (forward cards).
                # The /study web UI handles backward + shadowing natively.
                # Legacy cards (pre-SRS migration) had no `kind` field — treat
                # them as forward for backward-compat with old exports.
                kind = c.get("kind", "forward")
                if kind != "forward":
                    continue
                front = c.get("front") or ""
                back = c.get("back") or ""
                note = genanki.Note(
                    model=MODEL,
                    fields=[
                        front,
                        back,
                        c.get("lemma") or "",
                        c.get("pos") or "",
                        cap.it_text or "",
                        cap.en_transcript or "",
                        audio_tag,
                        cap.id,
                    ],
                    tags=["captured", *(c.get("tags") or [])],
                    guid=genanki.guid_for(cap.id, back, front),
                )
                deck.add_note(note)

        pkg = genanki.Package(deck)
        pkg.media_files = media_files
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # genanki writes synchronously; the media files exist for the duration
        # of this block, so the .apkg has been fully zipped before we exit.
        await asyncio.to_thread(pkg.write_to_file, str(out_path))
    return out_path


def build_package(captures: Iterable[Capture], out_path: Path) -> Path:
    """Sync wrapper around build_package_async for tests / sync call sites."""
    return asyncio.run(build_package_async(captures, out_path))


def output_path(name: str) -> Path:
    # On Cloud Run the project cwd is read-only; fall back to /tmp.
    try:
        settings.cards_dir.mkdir(parents=True, exist_ok=True)
        return settings.cards_dir / name
    except OSError:
        out_dir = Path("/tmp/cards")
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / name
