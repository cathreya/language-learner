"""Spaced repetition logic using py-fsrs (FSRS-6).

Each capture spawns multiple FSRS cards:
  - forward     : English → target (one per vocab item)
  - backward    : target → English (one per vocab item)
  - shadowing   : full sentence + audio (one per capture)

Cards are stored embedded in the Capture document (capture.cards is a list of
dicts). FSRS state per card lives at card["srs"] as a serialized FSRS Card dict.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler

_scheduler: Scheduler | None = None


def scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


# Rating slugs map → FSRS enum
RATING_FROM_SLUG: dict[str, Rating] = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def new_card_state() -> dict[str, Any]:
    """Create a fresh FSRS card state (dict) ready to be embedded."""
    return FsrsCard().to_dict()


def grade(srs_state: dict[str, Any], rating_slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply a grade. Returns (new_srs_state, review_log_dict)."""
    rating = RATING_FROM_SLUG.get(rating_slug.lower())
    if rating is None:
        raise ValueError(f"unknown rating: {rating_slug}")
    card = FsrsCard.from_dict(srs_state)
    card, log = scheduler().review_card(card, rating)
    return card.to_dict(), log.to_dict()


def due_now(srs_state: dict[str, Any], now: datetime | None = None) -> bool:
    if now is None:
        now = datetime.now(tz=timezone.utc)
    due_iso = srs_state.get("due")
    if not due_iso:
        return True
    try:
        due = datetime.fromisoformat(due_iso)
    except ValueError:
        return True
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return due <= now


def parse_due(srs_state: dict[str, Any]) -> datetime:
    """Best-effort parse of the `due` field; falls back to now for malformed state."""
    due_iso = srs_state.get("due")
    if not due_iso:
        return datetime.now(tz=timezone.utc)
    try:
        d = datetime.fromisoformat(due_iso)
    except ValueError:
        return datetime.now(tz=timezone.utc)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def make_vocab_cards(
    capture_id: str, vocab_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Turn the translator's vocab cards into (forward + backward) FSRS cards."""
    out: list[dict[str, Any]] = []
    for idx, v in enumerate(vocab_items):
        common = {
            "lemma": v.get("lemma") or "",
            "pos": v.get("pos") or "",
            "granularity": v.get("granularity") or "word",
            "tags": list(v.get("tags") or []),
        }
        front_en = v.get("front") or ""
        back_tg = v.get("back") or ""
        if not front_en or not back_tg:
            continue
        out.append(
            {
                "id": f"{capture_id}:fwd:{idx}",
                "kind": "forward",
                "front": front_en,
                "back": back_tg,
                **common,
                "srs": new_card_state(),
            }
        )
        out.append(
            {
                "id": f"{capture_id}:bwd:{idx}",
                "kind": "backward",
                "front": back_tg,
                "back": front_en,
                **common,
                "srs": new_card_state(),
            }
        )
    return out


def make_shadowing_card(capture_id: str) -> dict[str, Any]:
    return {
        "id": f"{capture_id}:shadow",
        "kind": "shadowing",
        "tags": ["captured", "shadowing"],
        "srs": new_card_state(),
    }
