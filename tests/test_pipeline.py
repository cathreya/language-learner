"""Unit tests for pipeline post-processing — no network, no Firestore."""

from __future__ import annotations

import pytest

from app.pipeline import (
    _attach_char_offsets,
    _backfill_trailing_punct,
    _drop_empty_cards,
    _merge_apostrophe_clitics,
    _strip_json_fence,
)


# === _merge_apostrophe_clitics ===

def test_merge_clitic_short_next():
    tokens = [
        {"surface": "Dov'", "lemma": "dovere", "gloss": "where", "pos": "verb", "is_word": True},
        {"surface": "è", "lemma": "essere", "gloss": "is", "pos": "verb", "is_word": True},
        {"surface": "la", "lemma": "il", "gloss": "the", "pos": "det", "is_word": True},
    ]
    out = _merge_apostrophe_clitics(tokens)
    assert out[0]["surface"] == "Dov'è"
    assert len(out) == 2


def test_merge_clitic_long_next():
    # `l'italiano` should still merge even though next word is 8 chars
    tokens = [
        {"surface": "l'", "lemma": "il", "gloss": "the", "pos": "det", "is_word": True},
        {"surface": "italiano", "lemma": "italiano", "gloss": "Italian", "pos": "noun", "is_word": True},
    ]
    out = _merge_apostrophe_clitics(tokens)
    assert out[0]["surface"] == "l'italiano"
    assert len(out) == 1


def test_merge_dedup_gloss():
    # Don't produce "where is is"
    tokens = [
        {"surface": "Dov'", "lemma": "dovere", "gloss": "where is", "pos": "verb", "is_word": True},
        {"surface": "è", "lemma": "essere", "gloss": "is", "pos": "verb", "is_word": True},
    ]
    out = _merge_apostrophe_clitics(tokens)
    assert "is is" not in out[0]["gloss"]


def test_merge_preserves_when_no_apostrophe():
    tokens = [
        {"surface": "Non", "lemma": "non", "gloss": "not", "pos": "adv", "is_word": True},
        {"surface": "lo", "lemma": "lo", "gloss": "it", "pos": "pron", "is_word": True},
    ]
    out = _merge_apostrophe_clitics(tokens)
    assert len(out) == 2
    assert out[0]["surface"] == "Non"
    assert out[1]["surface"] == "lo"


def test_merge_empty_list():
    assert _merge_apostrophe_clitics([]) == []
    single = [{"surface": "ciao", "is_word": True}]
    assert _merge_apostrophe_clitics(single) == single


# === _backfill_trailing_punct ===

def test_backfill_adds_question_mark():
    text = "Quanto costa?"
    tokens = [{"surface": "Quanto", "is_word": True}, {"surface": "costa", "is_word": True}]
    out = _backfill_trailing_punct(text, tokens)
    assert out[-1]["surface"] == "?"
    assert out[-1]["pos"] == "punct"
    assert out[-1]["is_word"] is False


def test_backfill_skipped_when_already_present():
    text = "Ciao!"
    tokens = [
        {"surface": "Ciao", "is_word": True},
        {"surface": "!", "is_word": False, "pos": "punct"},
    ]
    out = _backfill_trailing_punct(text, tokens)
    assert len(out) == 2  # no extra token added


def test_backfill_no_op_when_no_terminal_punct():
    text = "ciao"
    tokens = [{"surface": "ciao", "is_word": True}]
    out = _backfill_trailing_punct(text, tokens)
    assert len(out) == 1


# === _attach_char_offsets ===

def test_offsets_basic():
    text = "Non lo so."
    tokens = [
        {"surface": "Non", "is_word": True},
        {"surface": "lo", "is_word": True},
        {"surface": "so", "is_word": True},
        {"surface": ".", "is_word": False, "pos": "punct"},
    ]
    out = _attach_char_offsets(text, tokens)
    assert out[0]["char_start"] == 0 and out[0]["char_end"] == 3
    assert out[1]["char_start"] == 4 and out[1]["char_end"] == 6
    assert out[2]["char_start"] == 7 and out[2]["char_end"] == 9
    assert out[3]["char_start"] == 9 and out[3]["char_end"] == 10


def test_offsets_handle_missing_surface():
    text = "Boh"
    tokens = [
        {"surface": "Boh", "is_word": True},
        {"surface": "missing", "is_word": True},  # not in text
    ]
    # should not crash; the missing one gets a best-effort offset
    out = _attach_char_offsets(text, tokens)
    assert len(out) == 2
    assert out[0]["char_start"] == 0


# === _drop_empty_cards ===

def test_drop_empty_cards_filters():
    cards = [
        {"front": "good", "back": "buono"},
        {"front": "", "back": ""},
        {"front": " ", "back": "x"},
        {"front": "y", "back": " "},
        {"front": "to be", "back": "essere"},
    ]
    out = _drop_empty_cards(cards)
    assert [c["back"] for c in out] == ["buono", "essere"]


def test_drop_empty_cards_handles_missing_keys():
    assert _drop_empty_cards([{}]) == []
    assert _drop_empty_cards([{"front": None, "back": None}]) == []


# === _strip_json_fence ===

def test_strip_json_fence_plain():
    s = '{"a": 1}'
    assert _strip_json_fence(s) == '{"a": 1}'


def test_strip_json_fence_with_fence():
    s = '```json\n{"a": 1}\n```'
    assert _strip_json_fence(s) == '{"a": 1}'


def test_strip_json_fence_unlabeled():
    s = "```\n{\"a\": 1}\n```"
    assert _strip_json_fence(s) == '{"a": 1}'


# === Integration: full token postprocess pipeline ===

@pytest.mark.parametrize(
    "raw_text,raw_tokens,expected_surfaces",
    [
        (
            "Dov'è la stazione?",
            [
                {"surface": "Dov'", "is_word": True, "lemma": "dovere", "gloss": "where", "pos": "verb"},
                {"surface": "è", "is_word": True, "lemma": "essere", "gloss": "is", "pos": "verb"},
                {"surface": "la", "is_word": True, "lemma": "il", "gloss": "the", "pos": "det"},
                {"surface": "stazione", "is_word": True, "lemma": "stazione", "gloss": "station", "pos": "noun"},
                # missing trailing "?"
            ],
            ["Dov'è", "la", "stazione", "?"],
        ),
    ],
)
def test_full_postprocess(raw_text, raw_tokens, expected_surfaces):
    tokens = _merge_apostrophe_clitics(raw_tokens)
    tokens = _backfill_trailing_punct(raw_text, tokens)
    tokens = _attach_char_offsets(raw_text, tokens)
    assert [t["surface"] for t in tokens] == expected_surfaces
