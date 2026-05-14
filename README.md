# language-learner

LingQ for the sentences you actually say. Send a voice memo in English to a Telegram bot; it transcribes, translates to your target language (Italian by default), generates pronunciation audio, and produces Anki-importable vocab cards.

Capture in English while you live your life. Practice with what you actually use.

## Architecture (Cloud Run + serverless)

```
Telegram (voice msg)
   │
   ▼  webhook POST
┌──────────────────────────────────────────┐
│  Cloud Run (FastAPI, scales to zero)     │
│                                          │
│  /tg/webhook/{secret}                    │
│    ├── stage_transcribe   (Groq Whisper) │
│    ├── stage_translate    (Mistral Large)│
│    └── stage_tts          (Google TTS)   │
│                                          │
│  /r/{id}      → review page              │
│  /audio/{id}  → 302 → public GCS URL     │
│  /cards.apkg  → genanki package          │
└──────────────────────────────────────────┘
        │              │
        ▼              ▼
   Firestore        GCS bucket (audio mp3s, public-read)
   (captures docs)
```

## Setup

### Local development

1. **Install deps:** `uv sync`
2. **Fill `.env`** (copy from `.env.example`):
   - `TELEGRAM_BOT_TOKEN` — @BotFather → `/newbot`
   - `TELEGRAM_WEBHOOK_SECRET` — random string (`python -c 'import secrets;print(secrets.token_urlsafe(32))'`)
   - `GROQ_API_KEY` — https://console.groq.com/keys
   - `MISTRAL_API_KEY` — https://console.mistral.ai/api-keys
   - `GOOGLE_TTS_API_KEY` — GCP Console → Credentials → API key, enable Cloud TTS
3. **Validate the translate prompt** (optional but recommended):
   ```
   uv run python scripts/validate_prompt.py
   ```
4. **Run the server:**
   ```
   uv run uvicorn app.main:app --reload --port 8000
   ```
   - Web review: http://localhost:8000/
   - In local mode the bot uses the same webhook endpoint — point Telegram at it via ngrok or tailscale:
     ```
     # one-time
     ngrok http 8000
     # then register the webhook (replace <URL> with the ngrok https URL):
     curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
       -H 'Content-Type: application/json' \
       -d '{"url":"<URL>/tg/webhook/$TELEGRAM_WEBHOOK_SECRET","secret_token":"$TELEGRAM_WEBHOOK_SECRET"}'
     ```

### Production deploy (Cloud Run)

1. **GCP setup** — same project as your TTS key works:
   ```
   gcloud auth login
   gcloud auth application-default login        # local dev's GCS access
   gcloud config set project <YOUR_PROJECT_ID>
   gcloud services enable run.googleapis.com storage.googleapis.com \
       artifactregistry.googleapis.com cloudbuild.googleapis.com \
       texttospeech.googleapis.com secretmanager.googleapis.com
   ```

2. **Create Firestore DB** (one-time, in the same GCP project):
   ```
   gcloud firestore databases create --location=us-central1 --type=firestore-native
   ```

3. **Fill `.env` deploy fields:**
   ```
   PROJECT_ID=<gcp-project-id>
   REGION=us-central1                 # free-tier eligible US region
   SERVICE=it-practice-bot
   GCS_BUCKET=<globally-unique-name>  # e.g. it-practice-bot-audio
   ```

4. **Push secrets to Secret Manager:**
   ```
   bash scripts/setup_secrets.sh
   ```

5. **Deploy:**
   ```
   bash scripts/deploy_cloud_run.sh
   ```
   The script creates the GCS bucket if needed, deploys the service, sets `PUBLIC_BASE_URL` to the Cloud Run URL, and registers the Telegram webhook.

6. **DM the bot.** Voice memo → translation + audio + review URL.

## Workflow

- **Capture** — Hold Telegram's mic button. Release. Bot replies with "Got it — processing..." → 10-15s → Italian translation + voice memo + review-page link.
- **Shadow** — Open the review URL. Tap any Italian word for translation. Play audio, repeat aloud.
- **Drill** — `/export` in the bot OR click "Download new cards" on any review page. Import the .apkg into Anki / AnkiDroid.

## Bot commands

| Command | What |
|---------|------|
| `/start` | Intro. |
| `/list` | Last 10 captures + statuses. |
| `/retry <id>` | Re-run pipeline on a failed capture. 8-char prefix works. |
| `/delete <id>` | Soft-delete (won't show in lists or exports). |
| `/export` | DM the latest incremental `.apkg`. |

## Card export semantics

- **Incremental** (default): cards from captures not yet exported. Advances the `exported_at` cursor. Safe to re-import.
- **Snapshot** (`?snapshot=true` on the web endpoint): all captures, ad-hoc. Does NOT advance the cursor. Re-importing WILL duplicate.

## Switching target language

Change in `.env` / Cloud Run env vars:

```
TARGET_LANG=fr
TARGET_LANG_NAME=French
GOOGLE_TTS_VOICE=fr-FR-Chirp3-HD-Aoede
GOOGLE_TTS_LANGUAGE=fr-FR
```

The Mistral prompt and Anki deck are language-agnostic.

## TODO

- [ ] **Switch GCS bucket from public-read to signed URLs.** Currently audio URLs are guessable-only-if-you-know-the-128-bit-capture-id (effectively unguessable for personal use), but anyone with the URL can stream forever. Fine for solo use; switch before sharing the app with anyone.
- [ ] **Webhook timeout robustness.** Pipeline runs inline in the Telegram webhook handler (~15s typical). If Mistral is slow + Cloud Run cold-start coincide, we could approach Telegram's webhook timeout. Long-term fix: return 200 fast + run pipeline as background task with `CPU always allocated`.

## Stack

- **FastAPI** + **aiogram** (Telegram webhook)
- **Google Cloud Firestore** (NoSQL document store for captures) — free tier
- **Google Cloud Storage** (public-read audio bucket) — free tier
- **Google Cloud Run** (serverless, scales to zero) — free tier
- **Google Cloud TTS** Chirp HD voice (audio synthesis) — free tier
- **Groq** Whisper Large v3 Turbo (transcription) — free
- **Mistral Large** (translation + tokenization + cards) — free tier
- **genanki** (.apkg builder)
- **Pydantic** (Capture document model)
- **uv** (package manager)
