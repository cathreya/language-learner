"""Unit tests for SRS card construction + grading. No network."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import srs


def test_make_vocab_cards_pairs():
    vocab = [
        {"front": "to sleep", "back": "dormire", "lemma": "dormire", "pos": "verb"},
        {"front": "please", "back": "per favore", "lemma": "per favore", "pos": "phrase"},
    ]
    cards = srs.make_vocab_cards("CAPID", vocab)
    # 2 vocab × 2 directions = 4 cards
    assert len(cards) == 4
    kinds = {c["kind"] for c in cards}
    assert kinds == {"forward", "backward"}
    # Forward card 0: EN → IT
    fwd0 = [c for c in cards if c["id"] == "CAPID:fwd:0"][0]
    assert fwd0["front"] == "to sleep"
    assert fwd0["back"] == "dormire"
    # Backward card 0: IT → EN
    bwd0 = [c for c in cards if c["id"] == "CAPID:bwd:0"][0]
    assert bwd0["front"] == "dormire"
    assert bwd0["back"] == "to sleep"


def test_make_vocab_cards_skips_empty():
    vocab = [
        {"front": "", "back": "dormire"},
        {"front": "sleep", "back": ""},
        {"front": "to know", "back": "sapere"},
    ]
    cards = srs.make_vocab_cards("X", vocab)
    # Only the third one produces cards
    assert len(cards) == 2
    assert {c["back"] for c in cards} == {"sapere", "to know"}


def test_make_vocab_cards_attaches_initial_srs():
    cards = srs.make_vocab_cards(
        "Z", [{"front": "yes", "back": "sì", "lemma": "sì", "pos": "interj"}]
    )
    for c in cards:
        assert "srs" in c
        assert c["srs"].get("due")


def test_make_shadowing_card_structure():
    card = srs.make_shadowing_card("CAPID")
    assert card["kind"] == "shadowing"
    assert card["id"] == "CAPID:shadow"
    assert "shadowing" in card.get("tags", [])
    assert "srs" in card


def test_grade_advances_due_date():
    state = srs.new_card_state()
    before_due = state["due"]
    new_state, log = srs.grade(state, "good")
    assert new_state["due"] >= before_due
    assert log.get("rating") == 3  # Rating.Good = 3


def test_grade_rejects_bad_rating():
    state = srs.new_card_state()
    with pytest.raises(ValueError):
        srs.grade(state, "stellar")


def test_due_now_true_for_default_state():
    # Newly-created cards are due immediately
    state = srs.new_card_state()
    assert srs.due_now(state)


def test_due_now_false_for_future_due():
    state = srs.new_card_state()
    state["due"] = "2099-12-31T00:00:00+00:00"
    assert not srs.due_now(state)


def test_parse_due_handles_malformed():
    # Should fall back to "now" without raising
    state = {"due": "not-a-date"}
    d = srs.parse_due(state)
    assert isinstance(d, datetime)
    assert d.tzinfo is not None
