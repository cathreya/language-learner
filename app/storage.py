"""Audio storage backend.

In production (Cloud Run + GCS): audio files live in a public-readable GCS bucket.
The capture's `it_audio_path` stores either:
  - a `gs://bucket/object` URI for GCS-hosted files (production), OR
  - a local filesystem path for legacy/dev runs.

The helpers in this module abstract over both so the bot, web routes, and card
exporter don't need to care which backend produced a given file.

The `use_gcs()` switch is just "is GCS_BUCKET configured?". Locally you can set
it or not; if not, audio falls back to local disk under DATA_DIR/audio.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def use_gcs() -> bool:
    return bool(settings.gcs_bucket)


def _object_name(capture_id: str) -> str:
    prefix = settings.gcs_audio_prefix.rstrip("/")
    return f"{prefix}/{capture_id}.mp3"


def _vocab_object_name(capture_id: str, vocab_idx: int) -> str:
    prefix = settings.gcs_audio_prefix.rstrip("/")
    return f"{prefix}/{capture_id}/v{vocab_idx}.mp3"


def gcs_uri(capture_id: str) -> str:
    return f"gs://{settings.gcs_bucket}/{_object_name(capture_id)}"


def gcs_vocab_uri(capture_id: str, vocab_idx: int) -> str:
    return f"gs://{settings.gcs_bucket}/{_vocab_object_name(capture_id, vocab_idx)}"


def public_url(capture_id_or_uri: str) -> str:
    """Return the public HTTPS URL for a GCS-stored audio file.

    Accepts either a bare capture id or a `gs://bucket/path` URI.
    """
    if capture_id_or_uri.startswith("gs://"):
        # gs://bucket/path → https://storage.googleapis.com/bucket/path
        without_scheme = capture_id_or_uri[len("gs://") :]
        return f"https://storage.googleapis.com/{without_scheme}"
    return f"https://storage.googleapis.com/{settings.gcs_bucket}/{_object_name(capture_id_or_uri)}"


def _sync_upload_object(object_name: str, data: bytes, content_type: str) -> None:
    from google.cloud import storage  # local import — heavy

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    blob = bucket.blob(object_name)
    blob.cache_control = "public, max-age=31536000"
    blob.upload_from_string(data, content_type=content_type)


async def upload_audio(capture_id: str, data: bytes, content_type: str = "audio/mpeg") -> str:
    """Upload sentence audio for a capture; return the gs:// URI.

    Falls back to local disk if GCS isn't configured.
    """
    if use_gcs():
        await asyncio.to_thread(_sync_upload_object, _object_name(capture_id), data, content_type)
        return gcs_uri(capture_id)
    out = settings.audio_dir / f"{capture_id}.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return str(out)


async def upload_vocab_audio(
    capture_id: str, vocab_idx: int, data: bytes, content_type: str = "audio/mpeg"
) -> str:
    """Upload audio for a single vocab item; return the gs:// URI."""
    if use_gcs():
        await asyncio.to_thread(
            _sync_upload_object,
            _vocab_object_name(capture_id, vocab_idx),
            data,
            content_type,
        )
        return gcs_vocab_uri(capture_id, vocab_idx)
    out = settings.audio_dir / capture_id / f"v{vocab_idx}.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return str(out)


def _sync_download(capture_id_or_uri: str) -> bytes:
    from google.cloud import storage

    client = storage.Client()
    if capture_id_or_uri.startswith("gs://"):
        path = capture_id_or_uri[len("gs://") :]
        bucket_name, _, object_name = path.partition("/")
        bucket = client.bucket(bucket_name)
    else:
        bucket = client.bucket(settings.gcs_bucket)
        object_name = _object_name(capture_id_or_uri)
    return bucket.blob(object_name).download_as_bytes()


async def fetch_audio_bytes(path_or_uri: str) -> bytes:
    """Read audio bytes regardless of backend.

    `path_or_uri` is whatever was stored in `Capture.it_audio_path`:
      - `gs://...` → pull from GCS
      - anything else → read from local filesystem
    """
    if path_or_uri.startswith("gs://"):
        return await asyncio.to_thread(_sync_download, path_or_uri)
    return await asyncio.to_thread(functools.partial(Path(path_or_uri).read_bytes))


async def download_to_path(path_or_uri: str, dest: Path) -> Path:
    """Make sure the audio exists at `dest` on local disk; return the path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path_or_uri.startswith("gs://"):
        data = await fetch_audio_bytes(path_or_uri)
        dest.write_bytes(data)
    else:
        src = Path(path_or_uri)
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
    return dest
