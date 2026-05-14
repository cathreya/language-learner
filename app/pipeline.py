"""Capture processing pipeline: transcribe → translate → TTS.

Each stage updates Capture.status and persists outputs.
On failure, status=failed and error is populated.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from groq import AsyncGroq
from mistralai.client import Mistral

from app import db, srs
from app.config import settings
from app.models import Capture, CaptureStatus

logger = logging.getLogger(__name__)

_groq_client: AsyncGroq | None = None
_mistral_client: Mistral | None = None


def groq() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set in .env")
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


def mistral() -> Mistral:
    global _mistral_client
    if _mistral_client is None:
        if not settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY not set in .env")
        _mistral_client = Mistral(api_key=settings.mistral_api_key)
    return _mistral_client


TRANSLATE_SYSTEM_PROMPT = """You are a precise translator and linguistic analyzer for {target_name} language learners.

You receive an English sentence (or short utterance) that the user wants to learn how to say in {target_name}.

Return ONLY a JSON object with this exact shape:
{{
  "target_text": "the full {target_name} translation as a single string with natural punctuation",
  "tokens": [
    {{"surface": "word_or_punct_as_it_appears", "lemma": "dictionary_form", "gloss": "English meaning in this context", "pos": "noun|verb|adj|adv|pron|prep|det|conj|num|punct|interj|other", "is_word": true_or_false}}
  ],
  "cards": [
    {{"front": "English meaning to learn", "back": "{target_name} word or short phrase", "lemma": "dictionary form", "pos": "part of speech", "granularity": "word_or_phrase", "tags": ["captured"]}}
  ]
}}

CRITICAL RULES:
- target_text must be NATURAL, IDIOMATIC {target_name} — the way a native speaker would actually say it in casual conversation. Translate by INTENT, not by words.
  - Prefer single-word idiomatic forms over literal multi-word calques (e.g., "this morning" / "today morning" → "stamattina", NOT "oggi mattina"; "tonight" → "stasera", NOT "questa notte" unless really late).
  - Restructure freely when natural {target_name} word order differs from English.
  - If the English is awkward or non-native (e.g., "today morning"), translate what the speaker MEANT, not what they said.
  - NEVER invent words. NEVER make up verbs or inflections. If unsure, prefer a more common phrasing.
  - Match register: casual English stays casual; formal stays formal.
- tokens must cover EVERY character of target_text in left-to-right order. The concatenated surfaces (plus implicit single spaces between word-tokens that aren't adjacent to punctuation) MUST exactly reproduce target_text including all final punctuation (?, !, .).
- ALWAYS include the final punctuation (?, !, .) as its OWN token with is_word=false, pos="punct".
- Clitics and elisions in {target_name} ("dell'", "all'", "un'", "c'è", "Dov'è", "l'", "d'") MUST be ONE single token with the full surface including the apostrophe. NEVER split a contraction into two tokens.
- Treat hyphenated compounds as one token. Em-dashes and en-dashes are their own punct tokens.
- lemma is the dictionary form (singular masculine for nouns/adjectives, infinitive for verbs, base form for adverbs).
- gloss is the English meaning IN THIS CONTEXT (not a generic dictionary entry). 1-4 words.
- cards: extract the 1-6 most useful vocabulary items a learner should add to flashcards. Include both single words AND multi-word expressions/idioms. Skip ultra-basic function words (a, the, is, of, in, and). Set granularity="word" for single lemmas, "phrase" for multi-word expressions and idioms.
- CRITICAL: `front` (English) MUST grammatically match the form of `back` (target). Examples in {target_name}:
    - back="dormire" (infinitive) → front="to sleep"
    - back="ho dormito" (1st-person present-perfect) → front="I slept" or "I have slept"
    - back="ha detto" (3rd-person present-perfect) → front="he/she said"
    - back="detto" (past participle alone) → front="said"
    - back="andrai" (2nd-person future) → front="you will go"
    - back="andato" (past participle) → front="gone"
    - back="non" → front="not"
    - back="per favore" (idiom) → front="please"
  Match the inflection (tense, person, number, mood). Do NOT decline to the dictionary form on the English side.
- `lemma` stays the dictionary form regardless (so different conjugations share a lemma).
- Output ONLY the JSON object. No prose, no markdown fence, no commentary."""


_TRAILING_PUNCT = {".", "?", "!", "…"}


def _merge_apostrophe_clitics(tokens: list[dict]) -> list[dict]:
    """Merge a token ending in apostrophe with the next word-token.

    LLMs frequently split forms like `Dov'è`, `c'è`, `un'altra`, `dell'italiano`,
    `l'italiano`, `d'accordo` into two adjacent tokens. The first ends with `'`,
    the second is the elided word. Merge them into one tappable token so the
    popup shows the full surface and a combined gloss.
    """
    if len(tokens) < 2:
        return tokens
    out: list[dict] = []
    i = 0
    while i < len(tokens):
        cur = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        surface = cur.get("surface", "")
        if (
            nxt is not None
            and cur.get("is_word", True)
            and nxt.get("is_word", True)
            and surface.endswith("'")
            and not nxt.get("surface", "").startswith(" ")
        ):
            cur_gloss = (cur.get("gloss") or "").strip()
            nxt_gloss = (nxt.get("gloss") or "").strip()
            if cur_gloss and nxt_gloss and cur_gloss != nxt_gloss:
                combined_gloss = (
                    nxt_gloss if cur_gloss in nxt_gloss
                    else cur_gloss if nxt_gloss in cur_gloss
                    else f"{cur_gloss} {nxt_gloss}"
                )
            else:
                combined_gloss = cur_gloss or nxt_gloss
            merged = {
                **cur,
                "surface": surface + nxt.get("surface", ""),
                "lemma": cur.get("lemma") or nxt.get("lemma", ""),
                "gloss": combined_gloss,
                "pos": cur.get("pos") or nxt.get("pos", ""),
                "is_word": True,
            }
            out.append(merged)
            i += 2
        else:
            out.append(cur)
            i += 1
    return out


def _drop_empty_cards(cards: list[dict]) -> list[dict]:
    """Filter cards missing front/back text — LLMs occasionally emit empties."""
    return [c for c in cards if (c.get("front") or "").strip() and (c.get("back") or "").strip()]


def dedupe_cards(
    cards: list[dict], existing_keys: set[tuple[str, str]]
) -> tuple[list[dict], set[tuple[str, str]]]:
    """Drop cards whose (back_lower, pos_lower) already exists in `existing_keys`.

    Also dedupes within the input list (intra-capture dupes). Returns
    `(filtered_cards, updated_keys)` so callers can chain calls.
    """
    keys = set(existing_keys)
    out: list[dict] = []
    for c in cards:
        key = (
            (c.get("back") or "").strip().lower(),
            (c.get("pos") or "").strip().lower(),
        )
        if key[0] and key in keys:
            continue
        if key[0]:
            keys.add(key)
        out.append(c)
    return out, keys


def _backfill_trailing_punct(text: str, tokens: list[dict]) -> list[dict]:
    """If `text` ends with .?!… but the last word-token doesn't include it, append it."""
    text = text.rstrip()
    if not text:
        return tokens
    last = text[-1]
    if last not in _TRAILING_PUNCT:
        return tokens
    # Already covered by an existing trailing token?
    if tokens:
        for t in reversed(tokens):
            surface = t.get("surface", "")
            if not surface:
                continue
            if surface.endswith(last):
                return tokens
            break
    tokens = list(tokens)
    tokens.append({"surface": last, "lemma": last, "gloss": "", "pos": "punct", "is_word": False})
    return tokens


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text


def _attach_char_offsets(text: str, tokens: list[dict]) -> list[dict]:
    """Compute char_start/char_end for each token by scanning the target_text.

    Falls back gracefully when a surface cannot be located.
    """
    cursor = 0
    out = []
    for tok in tokens:
        surface = tok.get("surface", "")
        if not surface:
            out.append({**tok, "char_start": cursor, "char_end": cursor})
            continue
        idx = text.find(surface, cursor)
        if idx == -1:
            idx = text.find(surface)
        if idx == -1:
            out.append({**tok, "char_start": cursor, "char_end": cursor + len(surface)})
            cursor += len(surface)
        else:
            out.append({**tok, "char_start": idx, "char_end": idx + len(surface)})
            cursor = idx + len(surface)
    return out


async def stage_transcribe(capture_id: str) -> None:
    cap = await db.get(capture_id)
    if not cap:
        raise RuntimeError(f"capture {capture_id} not found")
    # If we already have the transcript (text-mode capture, or retry on a
    # previously-completed voice capture), skip Whisper entirely. The source
    # .ogg may not even exist anymore — Cloud Run /tmp doesn't persist across
    # cold starts.
    if cap.en_transcript:
        return
    if not cap.en_audio_path:
        raise RuntimeError(f"capture {capture_id} has neither transcript nor audio")
    await db.set_status(capture_id, CaptureStatus.transcribing)

    audio_path = Path(cap.en_audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file missing: {audio_path}")

    gq = groq()
    with audio_path.open("rb") as f:
        resp = await gq.audio.transcriptions.create(
            file=(audio_path.name, f.read()),
            model=settings.groq_stt_model,
            language=settings.source_lang,
            response_format="json",
            temperature=0,
        )
    transcript = (resp.text or "").strip()
    if not transcript:
        raise RuntimeError("empty transcript")

    await db.update(capture_id, en_transcript=transcript)


async def call_translate_llm(english: str) -> dict:
    """Call Mistral with the translate prompt. Returns parsed JSON dict.

    Retries on 429 (rate limit) with exponential backoff so that captures
    arriving in quick succession don't fail outright.
    """
    import asyncio

    client = mistral()
    prompt = TRANSLATE_SYSTEM_PROMPT.format(target_name=settings.target_lang_name)

    delays = [1.5, 3, 6, 12, 30]  # ~52s max wait across 5 retries
    last_err: Exception | None = None
    for attempt, delay in enumerate(delays):
        try:
            resp = await client.chat.complete_async(
                model=settings.mistral_llm_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": english},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = resp.choices[0].message.content or ""
            if isinstance(raw, list):
                raw = "".join(getattr(p, "text", "") or "" for p in raw)
            try:
                return json.loads(_strip_json_fence(raw))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"LLM returned invalid JSON: {e}\n{raw[:500]}") from e
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            is_rate_limit = "429" in msg or "rate_limited" in msg or "rate limit" in msg.lower()
            if not is_rate_limit or attempt == len(delays) - 1:
                last_err = e
                break
            logger.info("mistral 429 — sleeping %.1fs and retrying (attempt %d)", delay, attempt + 1)
            await asyncio.sleep(delay)
            last_err = e
    raise last_err if last_err else RuntimeError("translate failed")


async def stage_translate(capture_id: str) -> None:
    cap = await db.get(capture_id)
    if not cap or not cap.en_transcript:
        raise RuntimeError(f"capture {capture_id} missing transcript")
    await db.set_status(capture_id, CaptureStatus.translating)

    data = await call_translate_llm(cap.en_transcript)

    target_text = (data.get("target_text") or "").strip()
    tokens = data.get("tokens") or []
    cards = data.get("cards") or []
    if not target_text or not tokens:
        raise RuntimeError(f"translation response missing fields: {data}")

    tokens = _merge_apostrophe_clitics(tokens)
    tokens = _backfill_trailing_punct(target_text, tokens)
    tokens = _attach_char_offsets(target_text, tokens)
    cards = _drop_empty_cards(cards)

    # Prepend a SENTENCE card (full capture as one vocab item). This produces
    # forward+backward sentence cards via the same machinery as word vocab.
    sentence_card = {
        "front": cap.en_transcript or "",
        "back": target_text,
        "lemma": target_text,
        "pos": "sentence",
        "granularity": "sentence",
        "tags": ["captured", "sentence"],
    }
    cards = [sentence_card, *cards]

    # Dedupe: drop any vocab card whose (back, pos) already exists in another capture.
    # Different conjugations of the same lemma stay separate (only exact-surface matches drop).
    existing_keys = await db.existing_vocab_keys(exclude_capture_id=capture_id)
    cards, _ = dedupe_cards(cards, existing_keys)

    # Fan out vocab into forward/backward FSRS cards + a sentence-level shadowing card.
    # Re-using existing srs state on retry: build by id and preserve the prior srs dict if any.
    existing = await db.get(capture_id)
    prior_by_id: dict[str, dict] = {
        c.get("id"): c.get("srs")
        for c in ((existing.cards if existing else None) or [])
        if c.get("id")
    }
    fresh = srs.make_vocab_cards(capture_id, cards)
    fresh.append(srs.make_shadowing_card(capture_id))
    for c in fresh:
        if c["id"] in prior_by_id and prior_by_id[c["id"]]:
            c["srs"] = prior_by_id[c["id"]]

    await db.update(capture_id, it_text=target_text, tokens=tokens, cards=fresh)


async def stage_tts(capture_id: str) -> None:
    """Generate audio (sentence + per-vocab); persist to GCS forever after."""
    from app import storage
    from app.tts import synthesize_bytes

    cap = await db.get(capture_id)
    if not cap or not cap.it_text:
        raise RuntimeError(f"capture {capture_id} missing translation")

    await db.set_status(capture_id, CaptureStatus.tts)

    # === Sentence audio (used by shadowing card + as a fallback) ===
    sentence_uri = cap.it_audio_path
    if not sentence_uri or (not sentence_uri.startswith("gs://") and not Path(sentence_uri).exists()):
        audio_bytes = await synthesize_bytes(cap.it_text)
        sentence_uri = await storage.upload_audio(cap.id, audio_bytes)

    # === Per-vocab audio (used by forward + backward cards) ===
    # Walk cards once to find unique vocab indices and their Italian text.
    # The Italian text for a vocab pair is the `back` of the forward card.
    cards = list(cap.cards or [])
    italian_by_idx: dict[int, str] = {}
    for c in cards:
        if c.get("kind") != "forward":
            continue
        cid = c.get("id", "")
        parts = cid.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        idx = int(parts[1])
        text = (c.get("back") or "").strip()
        if text:
            italian_by_idx[idx] = text

    # Synthesize each vocab item (skip if a card already has audio_uri).
    have_audio: dict[int, str | None] = {}
    for c in cards:
        if c.get("kind") not in {"forward", "backward"}:
            continue
        cid = c.get("id", "")
        parts = cid.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        idx = int(parts[1])
        if c.get("audio_uri"):
            have_audio[idx] = c["audio_uri"]

    audio_uri_by_idx: dict[int, str] = {}
    for idx, text in italian_by_idx.items():
        if idx in have_audio and have_audio[idx]:
            audio_uri_by_idx[idx] = have_audio[idx]  # already done
            continue
        # If this vocab item IS the whole sentence, reuse the sentence audio
        # (skips a redundant TTS call). Comparison ignores trailing punctuation.
        if text.strip(".!?…") == (cap.it_text or "").strip(".!?…"):
            audio_uri_by_idx[idx] = sentence_uri
            continue
        try:
            data = await synthesize_bytes(text)
            uri = await storage.upload_vocab_audio(cap.id, idx, data)
            audio_uri_by_idx[idx] = uri
        except Exception as e:  # noqa: BLE001
            logger.warning("vocab tts failed for capture=%s idx=%d: %s", cap.id, idx, e)

    # Attach audio_uri to forward + backward cards by index.
    for c in cards:
        cid = c.get("id", "")
        kind = c.get("kind")
        if kind == "shadowing":
            c["audio_uri"] = sentence_uri
            continue
        if kind not in {"forward", "backward"}:
            continue
        parts = cid.rsplit(":", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        idx = int(parts[1])
        if idx in audio_uri_by_idx:
            c["audio_uri"] = audio_uri_by_idx[idx]

    await db.update(
        capture_id,
        it_audio_path=sentence_uri,
        cards=cards,
        status=CaptureStatus.ready.value,
    )


STAGE_ORDER: list[tuple[CaptureStatus, callable]] = [
    (CaptureStatus.transcribing, stage_transcribe),
    (CaptureStatus.translating, stage_translate),
    (CaptureStatus.tts, stage_tts),
]


async def _run_stage(capture_id: str, stage_func) -> None:
    """Run a single stage; on failure, mark capture as failed and re-raise.

    Wrapping each stage means even direct callers (admin scripts, retry endpoints)
    can never leave a capture in an intermediate state.
    """
    try:
        await stage_func(capture_id)
    except Exception as e:
        logger.exception("stage %s failed for %s", stage_func.__name__, capture_id)
        await db.set_status(
            capture_id, CaptureStatus.failed, error=f"{type(e).__name__}: {e}"
        )
        raise


async def run_pipeline(capture_id: str, on_ready=None, on_failure=None) -> None:
    """Run all stages in order. Status lands at ready or failed.

    `on_ready` and `on_failure` are async callbacks invoked with the final Capture.
    """
    try:
        await _run_stage(capture_id, stage_transcribe)
        await _run_stage(capture_id, stage_translate)
        await _run_stage(capture_id, stage_tts)
    except Exception:
        cap = await db.get(capture_id)
        if cap and on_failure:
            await on_failure(cap)
        return

    cap = await db.get(capture_id)
    if cap and on_ready:
        await on_ready(cap)


async def recovery_sweep(stale_minutes: int = 5) -> int:
    """Reset captures stuck in non-terminal states for >`stale_minutes`.

    Called on app startup. A capture stuck in transcribing/translating/tts means
    a prior container died mid-pipeline. We mark them failed so the user can
    `/retry` from Telegram.
    """
    from datetime import timedelta, timezone

    from google.cloud.firestore_v1.base_query import FieldFilter

    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=stale_minutes)
    stuck_states = ["transcribing", "translating", "tts"]
    n = 0
    for state in stuck_states:
        q = db._col().where(filter=FieldFilter("status", "==", state))
        async for snap in q.stream():
            cap = db._snap_to_capture(snap)
            created = cap.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and created > cutoff:
                continue  # too fresh — probably actively being processed
            await db.set_status(
                cap.id,
                CaptureStatus.failed,
                error=f"stuck in {state}; reset by recovery sweep",
            )
            n += 1
    if n:
        logger.info("recovery sweep: reset %d stuck captures", n)
    return n


def utcnow() -> datetime:
    return datetime.utcnow()
