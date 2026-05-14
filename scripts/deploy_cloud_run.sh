#!/usr/bin/env bash
# One-shot Cloud Run deploy. Run from project root: bash scripts/deploy_cloud_run.sh
#
# Prereqs (one time):
#   gcloud auth login
#   gcloud auth application-default login          # so local dev can hit GCS
#   gcloud config set project <YOUR_PROJECT_ID>
#   gcloud services enable run.googleapis.com storage.googleapis.com \
#                          artifactregistry.googleapis.com cloudbuild.googleapis.com \
#                          texttospeech.googleapis.com firestore.googleapis.com \
#                          secretmanager.googleapis.com
#   gcloud firestore databases create --location="$REGION" --type=firestore-native
#
# Required env vars (read from .env automatically; can also be exported):
#   PROJECT_ID, REGION (e.g. us-central1), SERVICE, GCS_BUCKET
#   TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET
#   GROQ_API_KEY, MISTRAL_API_KEY, GOOGLE_TTS_API_KEY

set -euo pipefail

# --- Load .env if present (does not override already-exported vars) ---
if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  . .env
  set +o allexport
fi

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID in .env or env}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-it-practice-bot}"
GCS_BUCKET="${GCS_BUCKET:?set GCS_BUCKET in .env}"

echo "==> Project:  $PROJECT_ID"
echo "==> Region:   $REGION"
echo "==> Service:  $SERVICE"
echo "==> Bucket:   $GCS_BUCKET"

# --- Sanity check: bucket exists (created by setup_secrets.sh) ---
if ! gcloud storage buckets describe "gs://$GCS_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "ERROR: bucket gs://$GCS_BUCKET not found. Run scripts/setup_secrets.sh first."
  exit 1
fi

# --- Deploy ---
echo "==> Building + deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --source=. \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --concurrency=4 \
  --min-instances=0 \
  --max-instances=2 \
  --timeout=120s \
  --set-env-vars="GCS_BUCKET=${GCS_BUCKET},\
GCP_PROJECT=${PROJECT_ID},\
ALLOWED_TELEGRAM_USER_ID=${ALLOWED_TELEGRAM_USER_ID:-},\
GROQ_STT_MODEL=${GROQ_STT_MODEL:-whisper-large-v3-turbo},\
MISTRAL_LLM_MODEL=${MISTRAL_LLM_MODEL:-mistral-large-latest},\
GOOGLE_TTS_VOICE=${GOOGLE_TTS_VOICE:-it-IT-Chirp3-HD-Aoede},\
GOOGLE_TTS_LANGUAGE=${GOOGLE_TTS_LANGUAGE:-it-IT},\
SOURCE_LANG=${SOURCE_LANG:-en},\
TARGET_LANG=${TARGET_LANG:-it},\
TARGET_LANG_NAME=${TARGET_LANG_NAME:-Italian}" \
  --set-secrets="\
TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,\
TELEGRAM_WEBHOOK_SECRET=telegram-webhook-secret:latest,\
GROQ_API_KEY=groq-api-key:latest,\
MISTRAL_API_KEY=mistral-api-key:latest,\
GOOGLE_TTS_API_KEY=google-tts-api-key:latest"

# --- Resolve service URL + set as PUBLIC_BASE_URL ---
URL=$(gcloud run services describe "$SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')

echo "==> Service URL: $URL"

gcloud run services update "$SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --update-env-vars="PUBLIC_BASE_URL=$URL" >/dev/null

# --- Register the Telegram webhook ---
WEBHOOK_URL="$URL/tg/webhook/$TELEGRAM_WEBHOOK_SECRET"
echo "==> Setting Telegram webhook to: $WEBHOOK_URL"
curl -s -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -H 'Content-Type: application/json' \
  -d "{
    \"url\": \"$WEBHOOK_URL\",
    \"secret_token\": \"$TELEGRAM_WEBHOOK_SECRET\",
    \"allowed_updates\": [\"message\"]
  }" | head -200
echo

echo "==> Done. Send a voice message to your bot to test."
