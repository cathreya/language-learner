#!/usr/bin/env bash
# Push the secrets from .env into Google Secret Manager.
# Run once before the first deploy. Idempotent — re-running upserts new versions.

set -euo pipefail

if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  . .env
  set +o allexport
fi

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID in .env or env}"

put_secret() {
  local NAME="$1"
  local VALUE="$2"
  if [ -z "$VALUE" ]; then
    echo "  -- $NAME: empty in .env, skipping"
    return
  fi
  if ! gcloud secrets describe "$NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  ++ creating secret $NAME"
    gcloud secrets create "$NAME" --project="$PROJECT_ID" --replication-policy=automatic
  fi
  printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" \
    --project="$PROJECT_ID" --data-file=- >/dev/null
  echo "  ✔ $NAME"
}

echo "==> Project: $PROJECT_ID"
echo "==> Enabling secretmanager API (idempotent)..."
gcloud services enable secretmanager.googleapis.com --project="$PROJECT_ID" >/dev/null

echo "==> Upserting secret versions..."
put_secret telegram-bot-token       "${TELEGRAM_BOT_TOKEN:-}"
put_secret telegram-webhook-secret  "${TELEGRAM_WEBHOOK_SECRET:-}"
put_secret groq-api-key             "${GROQ_API_KEY:-}"
put_secret mistral-api-key          "${MISTRAL_API_KEY:-}"
put_secret google-tts-api-key       "${GOOGLE_TTS_API_KEY:-}"

# Grant the Cloud Run runtime service account access to read each secret.
# Default Cloud Run SA on Cloud Run is the Compute Engine default SA.
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "==> Granting $SA secretAccessor on each secret..."
for NAME in telegram-bot-token telegram-webhook-secret groq-api-key mistral-api-key google-tts-api-key; do
  if gcloud secrets describe "$NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "$NAME" \
      --project="$PROJECT_ID" \
      --member="serviceAccount:$SA" \
      --role="roles/secretmanager.secretAccessor" >/dev/null
  fi
done

if [ -n "${GCS_BUCKET:-}" ]; then
  REGION="${REGION:-us-central1}"
  if ! gcloud storage buckets describe "gs://$GCS_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "==> Creating bucket gs://$GCS_BUCKET in $REGION ..."
    gcloud storage buckets create "gs://$GCS_BUCKET" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --uniform-bucket-level-access
  else
    echo "==> Bucket gs://$GCS_BUCKET already exists."
  fi

  echo "==> Granting public-read on gs://$GCS_BUCKET (audio URLs resolve without signing) ..."
  gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET" \
    --member="allUsers" \
    --role="roles/storage.objectViewer" >/dev/null

  echo "==> Granting $SA storage.objectAdmin on gs://$GCS_BUCKET (writes from Cloud Run) ..."
  gcloud storage buckets add-iam-policy-binding "gs://$GCS_BUCKET" \
    --member="serviceAccount:$SA" \
    --role="roles/storage.objectAdmin" >/dev/null
fi

echo "==> Granting $SA datastore.user (for Firestore reads/writes) on the project ..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA" \
  --role="roles/datastore.user" >/dev/null

echo "==> Creating Firestore composite indexes (idempotent; safe to re-run) ..."
# `gcloud firestore indexes composite create` errors loudly if the index already
# exists; we swallow that specific case and continue.
create_index() {
  local DESC="$1"
  shift
  local OUT
  # gcloud's CLI default for --database is the bare string "default" (no
  # parens), but the actual auto-created Firestore database is named
  # "(default)" with parens. Always pass --database explicitly with the
  # paren'd value, single-quoted so bash leaves the parens alone.
  local DB="${FIRESTORE_DATABASE:-(default)}"
  if OUT=$(gcloud firestore indexes composite create \
      --collection-group=captures \
      --query-scope=COLLECTION \
      --project="$PROJECT_ID" \
      --database="$DB" \
      "$@" 2>&1); then
    echo "  ✔ $DESC (creating; takes ~1-2 min to be queryable)"
  elif echo "$OUT" | grep -q -i "already exists"; then
    echo "  = $DESC (already exists)"
  else
    echo "  ✗ $DESC failed:"
    echo "$OUT" | sed 's/^/    /'
  fi
}

# index 1 — recent_visible: where deleted_at == null order by created_at desc
create_index "recent_visible" \
  --field-config="field-path=deleted_at,order=ASCENDING" \
  --field-config="field-path=created_at,order=DESCENDING"

# index 2 — ready_pending_export: status==ready, deleted_at==null, exported_at==null, order by created_at asc
create_index "ready_pending_export" \
  --field-config="field-path=status,order=ASCENDING" \
  --field-config="field-path=deleted_at,order=ASCENDING" \
  --field-config="field-path=exported_at,order=ASCENDING" \
  --field-config="field-path=created_at,order=ASCENDING"

# index 3 — all_ready_visible: status==ready, deleted_at==null, order by created_at asc
create_index "all_ready_visible" \
  --field-config="field-path=status,order=ASCENDING" \
  --field-config="field-path=deleted_at,order=ASCENDING" \
  --field-config="field-path=created_at,order=ASCENDING"

echo "==> Done. Secrets + IAM + indexes are queued. Re-run smoke.sh in ~2 min if first deploy."
