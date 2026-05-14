"""Google Cloud TTS via REST API + API key (simpler than service account JSON).

Returns audio bytes; storage decision (GCS vs local disk) lives in app.storage.
"""

from __future__ import annotations

import base64

import httpx

from app.config import settings

TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"


async def synthesize_bytes(text: str) -> bytes:
    if not settings.google_tts_api_key:
        raise RuntimeError("GOOGLE_TTS_API_KEY not set in .env")

    body = {
        "input": {"text": text},
        "voice": {
            "languageCode": settings.google_tts_language,
            "name": settings.google_tts_voice,
        },
        "audioConfig": {"audioEncoding": "MP3"},
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            TTS_ENDPOINT,
            params={"key": settings.google_tts_api_key},
            json=body,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"google tts {resp.status_code}: {resp.text[:400]}")
        data = resp.json()

    audio_b64 = data.get("audioContent")
    if not audio_b64:
        raise RuntimeError(f"google tts response missing audioContent: {data}")

    return base64.b64decode(audio_b64)
