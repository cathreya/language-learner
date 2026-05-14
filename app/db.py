"""Firestore-backed Capture store.

Uses the async Firestore client. Collection name is fixed (`captures`) but the
Firestore database id can be customized via FIRESTORE_DATABASE (defaults to
"(default)") for projects that have multi-database setups.

ADC is used for auth: locally via `gcloud auth application-default login`, on
Cloud Run via the runtime service account.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore_v1
from google.cloud.firestore_v1.async_client import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import settings
from app.models import Capture, CaptureStatus

logger = logging.getLogger(__name__)

COLLECTION = "captures"

_client: AsyncClient | None = None


def client() -> AsyncClient:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {}
        if settings.gcp_project:
            kwargs["project"] = settings.gcp_project
        if settings.firestore_database and settings.firestore_database != "(default)":
            kwargs["database"] = settings.firestore_database
        _client = AsyncClient(**kwargs)
    return _client


def _col():
    return client().collection(COLLECTION)


async def init_db() -> None:
    """No-op for Firestore — schemas are flexible.

    Composite indexes (if needed for compound queries) are declared in
    firestore.indexes.json and deployed via gcloud. First-time queries that hit
    a missing index will return an error with a direct link to auto-create it.
    """
    return


def _snap_to_capture(snap) -> Capture:
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return Capture.model_validate(data)


async def get(capture_id: str) -> Capture | None:
    snap = await _col().document(capture_id).get()
    if not snap.exists:
        return None
    return _snap_to_capture(snap)


async def save(cap: Capture) -> None:
    """Upsert the full Capture document."""
    data = cap.model_dump(mode="python", exclude={"id"})
    await _col().document(cap.id).set(data)


async def create(cap: Capture) -> None:
    """Insert; raises if the document already exists (use for idempotency)."""
    data = cap.model_dump(mode="python", exclude={"id"})
    await _col().document(cap.id).create(data)


async def update(capture_id: str, **fields: Any) -> None:
    """Partial update of named fields."""
    await _col().document(capture_id).update(fields)


async def set_status(
    capture_id: str, status: CaptureStatus, error: str | None = None
) -> None:
    updates: dict[str, Any] = {"status": status.value}
    if error is not None:
        updates["error"] = error
    await update(capture_id, **updates)


async def soft_delete(capture_id: str, when: datetime) -> None:
    await update(capture_id, deleted_at=when)


async def find_by_update_id(update_id: int) -> Capture | None:
    q = _col().where(filter=FieldFilter("telegram_update_id", "==", update_id)).limit(1)
    async for snap in q.stream():
        return _snap_to_capture(snap)
    return None


async def recent_visible(limit: int = 50) -> list[Capture]:
    """Captures not soft-deleted, newest first."""
    q = (
        _col()
        .where(filter=FieldFilter("deleted_at", "==", None))
        .order_by("created_at", direction=firestore_v1.Query.DESCENDING)
        .limit(limit)
    )
    return [_snap_to_capture(s) async for s in q.stream()]


async def find_by_id_prefix(prefix: str) -> list[Capture]:
    """Match captures whose id starts with the given prefix (for /retry, /delete shorthand)."""
    if not prefix:
        return []
    end = prefix + ""
    q = (
        _col()
        .where(filter=FieldFilter("__name__", ">=", _col().document(prefix)))
        .where(filter=FieldFilter("__name__", "<", _col().document(end)))
        .limit(5)
    )
    return [_snap_to_capture(s) async for s in q.stream()]


async def ready_pending_export() -> list[Capture]:
    """Ready, not deleted, not yet exported. Used for incremental .apkg."""
    q = (
        _col()
        .where(filter=FieldFilter("status", "==", CaptureStatus.ready.value))
        .where(filter=FieldFilter("deleted_at", "==", None))
        .where(filter=FieldFilter("exported_at", "==", None))
        .order_by("created_at", direction=firestore_v1.Query.ASCENDING)
    )
    return [_snap_to_capture(s) async for s in q.stream()]


async def all_ready_visible() -> list[Capture]:
    """All ready + not-deleted captures. Used for the snapshot .apkg."""
    q = (
        _col()
        .where(filter=FieldFilter("status", "==", CaptureStatus.ready.value))
        .where(filter=FieldFilter("deleted_at", "==", None))
        .order_by("created_at", direction=firestore_v1.Query.ASCENDING)
    )
    return [_snap_to_capture(s) async for s in q.stream()]


async def mark_exported(capture_ids: list[str], when: datetime) -> None:
    if not capture_ids:
        return
    batch = client().batch()
    for cid in capture_ids:
        batch.update(_col().document(cid), {"exported_at": when})
    await batch.commit()


async def update_card_srs(capture_id: str, card_id: str, new_srs: dict) -> bool:
    """Update one card's srs state inside a capture's cards array.

    Returns True if a card was updated, False otherwise.
    """
    cap = await get(capture_id)
    if not cap or not cap.cards:
        return False
    cards = list(cap.cards)
    found = False
    for i, c in enumerate(cards):
        if c.get("id") == card_id:
            cards[i] = {**c, "srs": new_srs}
            found = True
            break
    if not found:
        return False
    await update(capture_id, cards=cards)
    return True


async def patch_card(capture_id: str, card_id: str, fields: dict) -> bool:
    """Patch arbitrary fields on one card. Returns True if found+updated."""
    cap = await get(capture_id)
    if not cap or not cap.cards:
        return False
    cards = list(cap.cards)
    for i, c in enumerate(cards):
        if c.get("id") == card_id:
            cards[i] = {**c, **fields}
            await update(capture_id, cards=cards)
            return True
    return False


async def delete_card(capture_id: str, card_id: str) -> bool:
    """Remove a single card from a capture's cards array."""
    cap = await get(capture_id)
    if not cap or not cap.cards:
        return False
    cards = [c for c in cap.cards if c.get("id") != card_id]
    if len(cards) == len(cap.cards):
        return False
    await update(capture_id, cards=cards)
    return True


def _is_new_card(srs_state: dict) -> bool:
    """A 'new' card is one that has never been reviewed (no last_review timestamp)."""
    lr = srs_state.get("last_review")
    return not lr


def _was_introduced_today(srs_state: dict, today_utc) -> bool:
    """Was this card's FIRST review on `today_utc`?

    FSRS doesn't track first_review, so we approximate: a card whose last_review
    is today AND whose step is small (still in early-learning) was very likely
    introduced today. Imperfect but bounds the introduction rate.
    """
    last_review_iso = srs_state.get("last_review")
    if not last_review_iso:
        return False
    try:
        lr = datetime.fromisoformat(last_review_iso)
    except ValueError:
        return False
    if lr.tzinfo is None:
        lr = lr.replace(tzinfo=timezone.utc)
    return lr.date() == today_utc and (srs_state.get("step") or 0) <= 1


def select_due_cards(
    captures: list[Capture],
    now: datetime,
    daily_new_card_limit: int,
    limit: int = 50,
) -> list[tuple[str, dict, Capture]]:
    """Pure function: pick which cards to study now.

    Returns mature (already-reviewed) cards due now, plus up to
    `daily_new_card_limit` minus the count of new cards introduced today.
    Capped at `limit`. Mature first, then new in stable id order.
    """
    from app import srs as srs_module

    mature: list[tuple[datetime, str, dict, Capture]] = []
    new: list[tuple[datetime, str, dict, Capture]] = []
    today_utc = now.date()
    new_introduced_today = 0
    for cap in captures:
        for c in cap.cards or []:
            srs_state = c.get("srs") or {}
            due = srs_module.parse_due(srs_state)
            if _is_new_card(srs_state):
                new.append((due, cap.id, c, cap))
                continue
            if _was_introduced_today(srs_state, today_utc):
                new_introduced_today += 1
            if due <= now:
                mature.append((due, cap.id, c, cap))

    mature.sort(key=lambda t: t[0])
    new.sort(key=lambda t: (t[1], t[2].get("id", "")))

    remaining_new_quota = max(0, daily_new_card_limit - new_introduced_today)
    queue = mature + new[:remaining_new_quota]
    return [(cid, card, cap) for _due, cid, card, cap in queue[:limit]]


async def due_cards(now: datetime, limit: int = 50) -> list[tuple[str, dict, Capture]]:
    """Fetch captures from Firestore, then select what's due (pure helper handles the logic)."""
    from app.config import settings

    captures = await all_ready_visible()
    return select_due_cards(
        captures, now, settings.daily_new_card_limit, limit=limit
    )


async def existing_vocab_keys(exclude_capture_id: str | None = None) -> set[tuple[str, str]]:
    """Return a set of (back_lower, pos_lower) keys for every forward card
    across all non-deleted captures, optionally excluding one capture.

    Used by the translate stage to dedupe new vocab against the existing corpus.
    """
    captures = await all_ready_visible()
    seen: set[tuple[str, str]] = set()
    for cap in captures:
        if exclude_capture_id and cap.id == exclude_capture_id:
            continue
        for c in cap.cards or []:
            if c.get("kind") != "forward":
                continue
            back = (c.get("back") or "").strip().lower()
            pos = (c.get("pos") or "").strip().lower()
            if back:
                seen.add((back, pos))
    return seen


async def count_due(now: datetime) -> int:
    """Cards available to study now, respecting the daily new-card cap."""
    rows = await due_cards(now, limit=10_000)
    return len(rows)
