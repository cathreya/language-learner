"""Tests for the pure dedupe_cards helper."""

from __future__ import annotations

from app.pipeline import dedupe_cards


def _card(front: str, back: str, pos: str = "noun") -> dict:
    return {"front": front, "back": back, "pos": pos}


def test_dedupe_passthrough_when_empty_existing():
    cards = [_card("a", "uno"), _card("b", "due")]
    out, keys = dedupe_cards(cards, set())
    assert [c["back"] for c in out] == ["uno", "due"]
    assert keys == {("uno", "noun"), ("due", "noun")}


def test_dedupe_drops_exact_back_pos_match():
    existing = {("uno", "noun")}
    cards = [_card("a", "uno"), _card("b", "due")]
    out, _ = dedupe_cards(cards, existing)
    assert [c["back"] for c in out] == ["due"]


def test_dedupe_keeps_same_back_different_pos():
    # "vino" as noun (wine) vs "vino" as verb (they come) — keep both
    existing = {("vino", "noun")}
    cards = [_card("they come", "vino", pos="verb")]
    out, _ = dedupe_cards(cards, existing)
    assert len(out) == 1
    assert out[0]["pos"] == "verb"


def test_dedupe_keeps_different_conjugations():
    # User's key requirement: dire (infinitive) and ha detto (compound past) stay separate
    existing = {("dire", "verb")}
    cards = [_card("she said", "ha detto", pos="verb")]
    out, _ = dedupe_cards(cards, existing)
    assert len(out) == 1
    assert out[0]["back"] == "ha detto"


def test_dedupe_is_case_insensitive():
    existing = {("non", "adv")}
    cards = [_card("not", "NON", pos="ADV")]
    out, _ = dedupe_cards(cards, existing)
    assert out == []


def test_dedupe_strips_whitespace():
    existing = {("oggi", "adv")}
    cards = [_card("today", "  oggi  ", pos="adv")]
    out, _ = dedupe_cards(cards, existing)
    assert out == []


def test_dedupe_handles_intra_capture_dupes():
    # If Mistral returns "non" twice in the same capture, dedupe within
    cards = [_card("not", "non", pos="adv"), _card("no", "non", pos="adv")]
    out, _ = dedupe_cards(cards, set())
    assert len(out) == 1


def test_dedupe_passes_empty_back():
    # Cards with empty back are kept (filtered elsewhere by _drop_empty_cards)
    cards = [_card("ghost", ""), _card("a", "uno")]
    out, _ = dedupe_cards(cards, set())
    assert len(out) == 2


def test_dedupe_returns_updated_keys():
    cards = [_card("a", "uno"), _card("b", "due")]
    _, keys = dedupe_cards(cards, {("zero", "num")})
    assert keys == {("zero", "num"), ("uno", "noun"), ("due", "noun")}
