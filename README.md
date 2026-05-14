# language-learner

> **Capture the sentences you actually want to say. Learn them with audio + spaced repetition.**

A personal language-learning tool. DM a Telegram bot an English voice memo or text. It transcribes, translates to your target language (Italian by default), generates pronunciation audio for every word and the full sentence, and exposes everything in a mobile-first web app with FSRS-6 spaced repetition.

Built around one observation: **Duolingo teaches you their vocabulary; you want a system that teaches you yours.**

```
┌───────────────────┐    ┌────────────────────────────────────────────────┐
│  Telegram bot     │    │  Cloud Run (FastAPI, scales to zero)            │
│  voice or text ───┼──► │   stage_transcribe   Groq Whisper Large v3      │
│                   │    │   stage_translate    Mistral Large (idiomatic)  │
│                   │    │   stage_tts          Google Cloud TTS Chirp HD  │
└───────────────────┘    └────────────────────────────────────────────────┘
                                            │              │
                                            ▼              ▼
                              Firestore (captures)   GCS bucket (audio)
                                            │
                                            ▼
                              Web /study (FSRS-6 review)
                              Web /r/<id> (LingQ-style tap-translate)
                              .apkg export (Anki/Mochi)
```

## Why this exists

Existing tools force a tradeoff:
- **Duolingo** — fixed curriculum, not vocabulary you actually use
- **Anki** — perfect SRS but you have to manually build every card
- **LingQ** — great review UX but only for content you *consume*, not content you *produce*

This bridges the gap. The vocabulary you accumulate is, by construction, vocabulary you needed in real life.

## Features

- **Capture in English.** No cognitive load — you don't need to know the target word yet.
- **Voice memo or text.** Whichever is faster in the moment.
- **Idiomatic translation.** "I went to the gym this morning" → `Sono andato in palestra stamattina` (not literal `oggi mattina`).
- **Three card kinds per capture.** Forward (EN→target), backward (target→EN), and shadowing (full sentence + audio). Sentence-level cards plus per-vocab cards.
- **Per-card audio.** Reviewing "sapere" plays you "sapere" — not the whole containing sentence.
- **Conjugation-matched English.** `ha detto` → "she said", not "to say"; `andato` → "gone", not "to go".
- **Tap-to-translate review.** LingQ-style: tap any word in the Italian text for its English gloss.
- **FSRS-6 SRS in the browser.** No Anki round-trip needed. `.apkg` export still works as a backup.
- **Dedupe across captures.** If you capture `non` twice, the second instance is skipped (but `dire` and `ha detto` stay as separate cards — different forms are different learning targets).
- **Daily new-card cap.** Default 20, configurable. So you don't get overwhelmed.

## What this is NOT

- **Not multi-tenant.** Each deploy is single-user. There's an `ALLOWED_TELEGRAM_USER_ID` gate that silently drops messages from anyone else. The web routes are unauthenticated — guarded only by 128-bit random capture IDs. If you want a public-facing version, you'd need to add auth + per-user data partitioning + signed audio URLs.
- **Not a native mobile app.** It's a PWA-ish web page that works well on mobile Chrome. No App Store presence.
- **Not English-source by hard requirement** — `SOURCE_LANG` is configurable — but I've only tested English→Italian.
- **Not free at any meaningful scale.** For one user, every service used (Groq, Mistral, Google TTS, Cloud Run, Firestore, Cloud Storage) is within free tier. With more users the bills add up.

## What you'll need

Before you start, sign up for / install:

| | Why | Cost |
|---|---|---|
| Google Cloud account + billing enabled | Hosts the app, DB, audio, and TTS | ~$0 for personal use, but card required |
| `gcloud` CLI | Deploy script uses it | Free |
| Telegram account | DM the bot from your phone | Free |
| Groq API key | Speech-to-text (Whisper) | Free tier — generous |
| Mistral API key | Translation + linguistic analysis | Free tier — 1B tokens/mo |
| Python 3.13 + [uv](https://docs.astral.sh/uv/) | Local dev / running tests | Free |

You do not need: Anki, an Italian course, anything paid.

## 5-minute setup

```bash
# 1. Clone + install
git clone https://github.com/cathreya/language-learner.git
cd language-learner
uv sync

# 2. Get API keys
#    - Telegram bot:  @BotFather  →  /newbot
#    - Groq:          https://console.groq.com/keys
#    - Mistral:       https://console.mistral.ai/api-keys
#    - Google TTS:    GCP console → APIs & Services → Credentials → Create API key, enable Cloud TTS

# 3. Find your Telegram user ID (so only you can use the bot)
#    DM @userinfobot on Telegram, copy your numeric ID

# 4. Fill in .env (copy from .env.example)
cp .env.example .env
# edit .env with the 5 keys above + ALLOWED_TELEGRAM_USER_ID + PROJECT_ID + GCS_BUCKET

# 5. Verify the prompt produces good translations BEFORE deploying
uv run python scripts/validate_prompt.py
# Reads 20 inflection-heavy English sentences, prints Mistral's translations + cards.
# If anything looks wrong, tighten the prompt in app/pipeline.py before going live.

# 6. Set up GCP (one-time)
gcloud auth login
gcloud auth application-default login
gcloud config set project <YOUR_PROJECT_ID>
gcloud services enable run.googleapis.com storage.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com texttospeech.googleapis.com \
  firestore.googleapis.com
gcloud firestore databases create --location=us-central1 --type=firestore-native

# 7. Push secrets to GCP Secret Manager + create the GCS bucket + grant IAM
bash scripts/setup_secrets.sh

# 8. Deploy to Cloud Run + register the Telegram webhook
bash scripts/deploy_cloud_run.sh

# 9. Smoke test
bash scripts/smoke.sh
# Expect: 9/9 passed
```

DM your bot. Send a voice memo or text. You should get back the translation + an audio file + a review URL.

## Usage

**Capture:**
- Open your bot's chat on your phone. Hold the mic, say the English phrase, release. ~5-8 seconds.
- Or send text. (For when you're in a quiet place or want to capture something you read.)

**Practice:**
- The bot replies with the target-language audio + a review-page link.
- Open the review page (`/r/<id>`) — tap any word for translation, play the audio.
- Open `/study` — FSRS picks your due cards. Forward / backward / shadowing kinds rotate. Grade Again / Hard / Good / Easy (or press keys 1/2/3/4).

**Optional Anki export:**
- `GET /cards.apkg` (or the bot's `/export` command) gives you an Anki-importable file containing the forward cards with embedded audio.

## Bot commands

| Command | What |
|---|---|
| `/start` | Intro. |
| `/list` | Last 10 captures + statuses. |
| `/retry <id>` | Re-run pipeline on a failed capture. 8-char prefix works. |
| `/delete <id>` | Soft-delete (won't show in lists or exports). |
| `/export` | DM the latest incremental `.apkg`. |

## Switching languages

Change in `.env` / Cloud Run env vars:

```
TARGET_LANG=fr
TARGET_LANG_NAME=French
GOOGLE_TTS_VOICE=fr-FR-Chirp3-HD-Aoede
GOOGLE_TTS_LANGUAGE=fr-FR
```

The Mistral prompt and Anki note type are language-agnostic. I've only validated English→Italian. PRs welcome for other pairs.

## Local development

```bash
uv sync --group dev
uv run --group dev pytest tests/ -q     # 51 tests, ~1.1s
uv run uvicorn app.main:app --reload    # local server, no Cloud Run needed
```

For local Telegram testing without deploying, point the bot at your local server via [ngrok](https://ngrok.com):

```bash
ngrok http 8000
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d "url=https://<your-ngrok-id>.ngrok.io/tg/webhook/$TELEGRAM_WEBHOOK_SECRET" \
  -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

When you're done, re-run `bash scripts/deploy_cloud_run.sh` to restore the production webhook.

## Architecture pointers

| File | What it does |
|---|---|
| `app/main.py` | FastAPI app + lifespan, recovery sweep on startup |
| `app/bot.py` | Telegram webhook dispatch + commands + single-user gate |
| `app/pipeline.py` | `stage_transcribe → stage_translate → stage_tts` orchestration + idempotency + the Mistral prompt |
| `app/db.py` | Firestore client + `due_cards` SRS query + `find_by_id_prefix` |
| `app/srs.py` | py-fsrs wrapper, card generation per kind |
| `app/web.py` | `/`, `/study`, `/r/<id>`, `/audio/<id>.mp3`, `/cards.apkg`, `/api/card/*/edit\|delete` |
| `app/templates/` + `app/static/` | Jinja templates + vanilla JS for review/study |
| `tests/` | 51 pytest tests covering pure helpers + critical web endpoints |

## TODO

- **Signed GCS URLs.** Currently the audio bucket is `allUsers: objectViewer` — fine for a personal deploy with 128-bit obscure capture IDs, but switch to signed URLs before sharing the bucket with anyone you don't trust.
- **Webhook timeout robustness.** Pipeline runs inline in the Telegram webhook handler (~15s typical). If Mistral is slow + Cloud Run cold-start coincide, we could approach Telegram's webhook timeout. Long-term fix: return 200 immediately + run pipeline as a background task with Cloud Run `CPU always allocated`.
- **Word-level audio↔text sync.** Today the audio plays at sentence level; tapping a word only shows the translation popup. Forced alignment (via Whisper-on-TTS-output) would let words highlight as they're spoken in shadowing.
- **Concurrency safety on `capture.cards`.** Read-modify-write on the cards array isn't transactional. Fine for one user; race-prone if you ever support more.

## License

[MIT](LICENSE). Built on [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs), [aiogram](https://github.com/aiogram/aiogram), [FastAPI](https://fastapi.tiangolo.com/), [SQLModel-shaped Pydantic](https://docs.pydantic.dev/) (Pydantic only — SQLModel was an interim step), and a lot of API calls to Groq, Mistral, and Google Cloud.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: bug fixes and other-language-pair support welcome; SaaS-shaped changes won't be merged because the tool is intentionally single-user.
