"""Tests for web routes — TestClient + monkeypatched db/tts. No Firestore, no network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Capture, CaptureStatus


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def fake_capture():
    return Capture(
        id="abc123def4567890",
        status=CaptureStatus.ready,
        en_transcript="I went to the gym",
        it_text="Sono andato in palestra",
        it_audio_path="gs://bucket/audio/abc123def4567890.mp3",
        cards=[
            {
                "id": "abc123def4567890:fwd:0",
                "kind": "forward",
                "front": "to go",
                "back": "andare",
                "lemma": "andare",
                "pos": "verb",
                "audio_uri": "gs://bucket/audio/abc123def4567890/v0.mp3",
            },
            {
                "id": "abc123def4567890:bwd:0",
                "kind": "backward",
                "front": "andare",
                "back": "to go",
                "lemma": "andare",
                "pos": "verb",
                "audio_uri": "gs://bucket/audio/abc123def4567890/v0.mp3",
            },
            {
                "id": "abc123def4567890:shadow",
                "kind": "shadowing",
                "tags": ["shadowing"],
            },
        ],
    )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_delete_missing_card(client, monkeypatch, fake_capture):
    async def _delete_card(capture_id, card_id):
        return False

    monkeypatch.setattr("app.db.delete_card", _delete_card)
    r = client.delete("/api/card/abc123def4567890/nonexistent")
    assert r.status_code == 404


def test_delete_card_ok(client, monkeypatch):
    deleted: list[tuple[str, str]] = []

    async def _delete_card(capture_id, card_id):
        deleted.append((capture_id, card_id))
        return True

    monkeypatch.setattr("app.db.delete_card", _delete_card)
    r = client.delete("/api/card/abc123def4567890/fwd:0")
    assert r.status_code == 200
    assert deleted == [("abc123def4567890", "abc123def4567890:fwd:0")]


def test_edit_card_regenerates_audio_when_italian_changes(client, monkeypatch, fake_capture):
    """Critical end-to-end test for the edit endpoint:
    1. Edit the back text of a forward card
    2. Verify TTS is called for the new Italian text
    3. Verify the new audio_uri is patched on the edited card AND its sibling
    """
    tts_calls: list[str] = []
    uploads: list[tuple[str, int, bytes]] = []
    patches: list[tuple[str, str, dict]] = []

    async def _get(cid):
        return fake_capture if cid == fake_capture.id else None

    async def _synth(text: str) -> bytes:
        tts_calls.append(text)
        return b"FAKEMP3"

    async def _upload(capture_id, vocab_idx, data, content_type="audio/mpeg"):
        uri = f"gs://bucket/audio/{capture_id}/v{vocab_idx}.mp3"
        uploads.append((capture_id, vocab_idx, data))
        return uri

    async def _patch_card(capture_id, card_id, fields):
        patches.append((capture_id, card_id, fields))
        return True

    monkeypatch.setattr("app.db.get", _get)
    monkeypatch.setattr("app.tts.synthesize_bytes", _synth)
    monkeypatch.setattr("app.storage.upload_vocab_audio", _upload)
    monkeypatch.setattr("app.db.patch_card", _patch_card)

    r = client.post(
        "/api/card/abc123def4567890/fwd:0/edit",
        json={"front": "to go", "back": "camminare"},  # change Italian
    )

    assert r.status_code == 200, r.text
    # TTS called once with the new Italian text
    assert tts_calls == ["camminare"]
    # Audio uploaded
    assert len(uploads) == 1
    # Two patches: the edited card + the sibling (backward card at same idx)
    assert len(patches) == 2
    edited_call = patches[0]
    sibling_call = patches[1]
    assert edited_call[1] == "abc123def4567890:fwd:0"
    assert sibling_call[1] == "abc123def4567890:bwd:0"
    # Both should have the same new audio_uri
    assert edited_call[2].get("audio_uri") == sibling_call[2].get("audio_uri")
    assert edited_call[2].get("back") == "camminare"


def test_edit_card_no_audio_regen_when_italian_unchanged(client, monkeypatch, fake_capture):
    """If only the English changes, TTS should NOT be called (audio is for Italian only)."""
    tts_calls: list[str] = []
    patches: list[tuple] = []

    async def _get(cid):
        return fake_capture

    async def _synth(text):
        tts_calls.append(text)
        return b"FAKE"

    async def _patch_card(capture_id, card_id, fields):
        patches.append((capture_id, card_id, fields))
        return True

    monkeypatch.setattr("app.db.get", _get)
    monkeypatch.setattr("app.tts.synthesize_bytes", _synth)
    monkeypatch.setattr("app.db.patch_card", _patch_card)

    # Change only the front (English)
    r = client.post(
        "/api/card/abc123def4567890/fwd:0/edit",
        json={"front": "to walk away", "back": "andare"},  # back unchanged
    )
    assert r.status_code == 200, r.text
    assert tts_calls == []
    # Only one patch (the edited card) — sibling mirror only fires when audio_uri changes
    assert len(patches) == 1


def test_edit_card_missing_returns_404(client, monkeypatch):
    async def _get(cid):
        return None

    monkeypatch.setattr("app.db.get", _get)
    r = client.post(
        "/api/card/zzz/fwd:0/edit",
        json={"front": "x", "back": "y"},
    )
    assert r.status_code == 404


def test_edit_card_requires_existing_card(client, monkeypatch, fake_capture):
    """If the card_id doesn't match anything in the capture, return 404."""

    async def _get(cid):
        return fake_capture

    monkeypatch.setattr("app.db.get", _get)
    r = client.post(
        "/api/card/abc123def4567890/fwd:99/edit",  # idx 99 doesn't exist
        json={"front": "x", "back": "y"},
    )
    assert r.status_code == 404
