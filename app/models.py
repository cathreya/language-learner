"""Pydantic models that map cleanly to/from Firestore documents.

Firestore stores native datetimes, lists, dicts, etc. — no JSON-string encoding
needed. The Capture model below is a plain document.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaptureStatus(str, Enum):
    pending = "pending"
    transcribing = "transcribing"
    translating = "translating"
    tts = "tts"
    ready = "ready"
    failed = "failed"


class Capture(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: datetime | None = None
    exported_at: datetime | None = None

    status: CaptureStatus = CaptureStatus.pending
    error: str | None = None

    telegram_user_id: int | None = None
    telegram_chat_id: int | None = None
    # Idempotency key for Telegram webhook retries. Stores msg.message_id (chat-
    # scoped — unique per chat). Field name preserved for backward compat with
    # already-stored documents; semantically it's a message id, not an update id.
    telegram_update_id: int | None = None

    en_audio_path: str | None = None
    en_transcript: str | None = None

    it_text: str | None = None
    it_audio_path: str | None = None

    tokens: list[dict[str, Any]] | None = None
    cards: list[dict[str, Any]] | None = None
