"""Tests for select_due_cards — the pure new-card-cap logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import select_due_cards
from app.models import Capture, CaptureStatus


def _now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _capture(id: str, cards: list[dict]) -> Capture:
    return Capture(
        id=id,
        status=CaptureStatus.ready,
        cards=cards,
        en_transcript="x",
        it_text="y",
    )


def _new_card(card_id: str, due: datetime | None = None) -> dict:
    return {
        "id": card_id,
        "kind": "forward",
        "front": "x",
        "back": "y",
        "srs": {
            "due": (due or _now()).isoformat(),
            "state": 1,
            "step": 0,
            "stability": None,
            "difficulty": None,
            "last_review": None,
        },
    }


def _reviewed_card(card_id: str, due: datetime, last_review: datetime, step: int = 2) -> dict:
    return {
        "id": card_id,
        "kind": "forward",
        "front": "x",
        "back": "y",
        "srs": {
            "due": due.isoformat(),
            "state": 2,
            "step": step,
            "stability": 5.0,
            "difficulty": 5.0,
            "last_review": last_review.isoformat(),
        },
    }


def test_no_cards_yields_empty_queue():
    out = select_due_cards([], _now(), daily_new_card_limit=20)
    assert out == []


def test_new_cards_capped_by_daily_limit():
    # 50 new cards, daily cap of 5 → only 5 should appear
    cards = [_new_card(f"c{i}") for i in range(50)]
    cap = _capture("CAP", cards)
    out = select_due_cards([cap], _now(), daily_new_card_limit=5)
    assert len(out) == 5


def test_mature_cards_not_capped():
    # 50 mature cards all due → all should appear (up to session limit)
    yesterday = _now() - timedelta(days=2)
    cards = [
        _reviewed_card(f"c{i}", due=_now() - timedelta(hours=1), last_review=yesterday)
        for i in range(30)
    ]
    cap = _capture("CAP", cards)
    out = select_due_cards([cap], _now(), daily_new_card_limit=5, limit=100)
    # daily cap of 5 should NOT shrink mature cards — only new ones
    assert len(out) == 30


def test_mature_first_then_new():
    yesterday = _now() - timedelta(days=2)
    mature = _reviewed_card("MATURE", due=_now() - timedelta(hours=1), last_review=yesterday)
    new = _new_card("NEW")
    cap = _capture("CAP", [new, mature])
    out = select_due_cards([cap], _now(), daily_new_card_limit=10)
    kinds = [card.get("id") for _cid, card, _cap in out]
    # mature comes first
    assert kinds == ["MATURE", "NEW"]


def test_future_due_excludes_mature():
    yesterday = _now() - timedelta(days=2)
    far_future = _reviewed_card(
        "FUTURE", due=_now() + timedelta(days=7), last_review=yesterday
    )
    cap = _capture("CAP", [far_future])
    out = select_due_cards([cap], _now(), daily_new_card_limit=10)
    assert out == []  # not yet due


def test_session_limit_truncates():
    cards = [_new_card(f"c{i}") for i in range(100)]
    cap = _capture("CAP", cards)
    out = select_due_cards([cap], _now(), daily_new_card_limit=200, limit=10)
    assert len(out) == 10


def test_cards_already_introduced_today_reduce_remaining_quota():
    # 3 cards reviewed earlier today (counts as introduced) + 10 new
    # Daily cap of 5 → only 2 more new should appear
    earlier_today = _now() - timedelta(hours=4)
    cards = [
        _reviewed_card(f"intro{i}", due=_now() + timedelta(hours=10), last_review=earlier_today, step=1)
        for i in range(3)
    ]
    cards += [_new_card(f"new{i}") for i in range(10)]
    cap = _capture("CAP", cards)
    out = select_due_cards([cap], _now(), daily_new_card_limit=5)
    # 0 mature (introduced cards not yet due) + (5 - 3) = 2 new = 2 total
    assert len(out) == 2


def test_handles_missing_srs_gracefully():
    cap = _capture("CAP", [{"id": "no_srs", "kind": "forward"}])
    # Should not raise; card with no srs is treated as new+due
    out = select_due_cards([cap], _now(), daily_new_card_limit=10)
    assert len(out) == 1
