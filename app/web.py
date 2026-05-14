"""FastAPI routes: review page, audio serving, card export."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import cards as cards_module
from app import db, srs, storage
from app.config import settings
from app.models import Capture

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _build_segments(cap: Capture) -> list[dict]:
    """Flatten tokens + whitespace into a renderable list of segments.

    Each segment is `{"kind": "tok"|"punct"|"ws", "text": str, ...token fields}`.
    """
    if not cap.it_text:
        return []
    tokens = cap.tokens or []
    if not tokens:
        return [{"kind": "ws", "text": cap.it_text}]

    out: list[dict] = []
    cursor = 0
    for t in tokens:
        start = t.get("char_start", cursor)
        end = t.get("char_end", start + len(t.get("surface", "")))
        if start > cursor:
            out.append({"kind": "ws", "text": cap.it_text[cursor:start]})
        if t.get("is_word", True):
            out.append(
                {
                    "kind": "tok",
                    "text": t.get("surface", ""),
                    "gloss": t.get("gloss", ""),
                    "lemma": t.get("lemma", ""),
                    "pos": t.get("pos", ""),
                }
            )
        else:
            out.append({"kind": "punct", "text": t.get("surface", "")})
        cursor = end
    if cursor < len(cap.it_text):
        out.append({"kind": "ws", "text": cap.it_text[cursor:]})
    return out


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    rows = await db.recent_visible(limit=50)
    due = await db.count_due(datetime.now(tz=timezone.utc))
    return templates.TemplateResponse(
        request,
        "index.html",
        {"captures": rows, "due_count": due, "base_url": settings.public_base_url},
    )


def _study_segments(cap: Capture) -> list[dict]:
    """Same tokenization as the review page (extracted to share rendering)."""
    return _build_segments(cap)


@router.get("/study", response_class=HTMLResponse)
async def study(request: Request) -> HTMLResponse:
    now = datetime.now(tz=timezone.utc)
    due = await db.due_cards(now, limit=50)
    queue: list[dict] = []
    for capture_id, card, cap in due:
        kind = card.get("kind")
        # Pick the right audio for this kind of card:
        #  - forward / backward → per-vocab audio (just that Italian word)
        #  - shadowing → full sentence audio
        if kind == "shadowing":
            audio_src = cap.it_audio_path
        else:
            audio_src = card.get("audio_uri") or cap.it_audio_path
        if audio_src and audio_src.startswith("gs://"):
            audio_url = storage.public_url(audio_src)
        elif audio_src:
            audio_url = f"/audio/{cap.id}.mp3"  # local-dev fallback
        else:
            audio_url = None

        item = {
            "capture_id": capture_id,
            "card_id": card.get("id"),
            "kind": kind,
            "front": card.get("front", ""),
            "back": card.get("back", ""),
            "lemma": card.get("lemma", ""),
            "pos": card.get("pos", ""),
            "granularity": card.get("granularity", ""),
            "tags": card.get("tags") or [],
            "it_text": cap.it_text or "",
            "en_transcript": cap.en_transcript or "",
            "segments": _build_segments(cap),
            "audio_url": audio_url,
        }
        queue.append(item)
    return templates.TemplateResponse(
        request,
        "study.html",
        {"queue": queue, "due_count": len(queue), "base_url": settings.public_base_url},
    )


@router.delete("/api/card/{capture_id}/{card_id_suffix:path}")
async def api_delete_card(capture_id: str, card_id_suffix: str) -> JSONResponse:
    """Delete a single card from a capture. Other cards in the same capture stay."""
    card_id = f"{capture_id}:{card_id_suffix}"
    ok = await db.delete_card(capture_id, card_id)
    if not ok:
        raise HTTPException(404, "card not found")
    return JSONResponse({"ok": True})


@router.post("/api/card/{capture_id}/{card_id_suffix:path}/edit")
async def api_edit_card(
    capture_id: str,
    card_id_suffix: str,
    payload: dict = Body(...),
) -> JSONResponse:
    """Edit a card's front/back (and lemma/pos optionally) + regenerate audio
    if the Italian (back-for-forward, front-for-backward) text changed.

    Body: {front?: str, back?: str, lemma?: str, pos?: str}
    """
    from app import storage
    from app.tts import synthesize_bytes

    card_id = f"{capture_id}:{card_id_suffix}"
    cap = await db.get(capture_id)
    if not cap or not cap.cards:
        raise HTTPException(404, "capture not found")
    target = None
    for c in cap.cards:
        if c.get("id") == card_id:
            target = c
            break
    if target is None:
        raise HTTPException(404, "card not found")

    new_front = (payload.get("front") or target.get("front") or "").strip()
    new_back = (payload.get("back") or target.get("back") or "").strip()
    new_lemma = (payload.get("lemma") or target.get("lemma") or "").strip()
    new_pos = (payload.get("pos") or target.get("pos") or "").strip()

    if not new_front or not new_back:
        raise HTTPException(400, "front and back are required")

    kind = target.get("kind")
    # The Italian side depends on direction.
    new_italian_text = new_back if kind == "forward" else new_front if kind == "backward" else None

    # Regenerate per-vocab audio if the Italian text changed (forward/backward only).
    audio_uri = target.get("audio_uri")
    if kind in {"forward", "backward"} and new_italian_text:
        old_italian = (target.get("back") if kind == "forward" else target.get("front")) or ""
        if new_italian_text != old_italian:
            # idx is the trailing number in the card id (e.g. capture_id:fwd:3 → 3)
            parts = card_id.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                idx = int(parts[1])
                try:
                    data = await synthesize_bytes(new_italian_text)
                    audio_uri = await storage.upload_vocab_audio(capture_id, idx, data)
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(500, f"tts failed: {e}") from e

    await db.patch_card(
        capture_id,
        card_id,
        {
            "front": new_front,
            "back": new_back,
            "lemma": new_lemma,
            "pos": new_pos,
            "audio_uri": audio_uri,
        },
    )

    # Mirror the audio update to the sibling card (forward↔backward share audio at same idx).
    if kind in {"forward", "backward"} and audio_uri:
        sibling_kind = "backward" if kind == "forward" else "forward"
        parts = card_id.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            idx = parts[1]
            sibling_id = f"{capture_id}:{'bwd' if sibling_kind == 'backward' else 'fwd'}:{idx}"
            await db.patch_card(capture_id, sibling_id, {"audio_uri": audio_uri})

    return JSONResponse({"ok": True})


@router.post("/api/capture/{capture_id}/reprocess")
async def api_reprocess(capture_id: str) -> JSONResponse:
    """Re-run translate + tts for a capture (fresh prompt run, dedupe re-applied).
    Preserves any srs state on cards that survive."""
    from app import pipeline

    cap = await db.get(capture_id)
    if not cap:
        raise HTTPException(404, "capture not found")
    try:
        await pipeline.stage_translate(capture_id)
        await pipeline.stage_tts(capture_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e
    return JSONResponse({"ok": True})


@router.post("/grade/{capture_id}/{card_id_suffix:path}")
async def grade(capture_id: str, card_id_suffix: str, rating: str) -> JSONResponse:
    """Apply a grade (again|hard|good|easy) to the named card."""
    card_id = f"{capture_id}:{card_id_suffix}"
    cap = await db.get(capture_id)
    if not cap or not cap.cards:
        raise HTTPException(404, "capture or cards not found")
    target = None
    for c in cap.cards:
        if c.get("id") == card_id:
            target = c
            break
    if target is None:
        raise HTTPException(404, "card not found")
    try:
        new_srs, _log = srs.grade(target.get("srs") or srs.new_card_state(), rating)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    ok = await db.update_card_srs(capture_id, card_id, new_srs)
    if not ok:
        raise HTTPException(500, "card update failed")
    return JSONResponse({"ok": True, "due": new_srs.get("due")})


@router.get("/r/{capture_id}", response_class=HTMLResponse)
async def review(capture_id: str, request: Request) -> HTMLResponse:
    cap = await db.get(capture_id)
    if not cap or cap.deleted_at is not None:
        raise HTTPException(404, "capture not found")
    segments = _build_segments(cap)
    return templates.TemplateResponse(
        request,
        "review.html",
        {"cap": cap, "segments": segments, "base_url": settings.public_base_url},
    )


@router.get("/audio/{capture_id}.mp3")
async def audio(capture_id: str):
    cap = await db.get(capture_id)
    if not cap or cap.deleted_at is not None or not cap.it_audio_path:
        raise HTTPException(404, "audio not found")
    if cap.it_audio_path.startswith("gs://"):
        return RedirectResponse(storage.public_url(cap.it_audio_path), status_code=302)
    p = Path(cap.it_audio_path)
    if not p.exists():
        raise HTTPException(404, "audio file missing on disk")
    return FileResponse(p, media_type="audio/mpeg")


@router.get("/cards.apkg")
async def export_cards(snapshot: bool = False) -> FileResponse:
    """Export captured cards as an Anki .apkg.

    - default: cards from captures not yet exported (advances `exported_at` cursor)
    - ?snapshot=true: ALL captures, ad-hoc — does NOT advance cursor; re-importing duplicates
    """
    if snapshot:
        rows = await db.all_ready_visible()
    else:
        rows = await db.ready_pending_export()
    if not rows:
        raise HTTPException(404, "no cards to export")

    suffix = "snapshot" if snapshot else "incremental"
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out = cards_module.output_path(f"italian-captured-{ts}-{suffix}.apkg")
    await cards_module.build_package_async(rows, out)

    if not snapshot:
        await db.mark_exported([r.id for r in rows], datetime.utcnow())

    return FileResponse(
        out,
        media_type="application/octet-stream",
        filename=out.name,
    )
