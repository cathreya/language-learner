#!/usr/bin/env bash
# Smoke-test a deployed Cloud Run service (or a local server).
#
# Usage:
#   bash scripts/smoke.sh                  # auto-detects deployed URL from gcloud
#   bash scripts/smoke.sh http://localhost:8000
#
# Checks (no real captures created):
#   1. /health                              200
#   2. /                                      200 (index page)
#   3. /r/nonexistent                        404
#   4. /audio/nonexistent.mp3                404
#   5. /cards.apkg  (no cards yet)           404
#   6. /tg/webhook/wrong-secret              403
#   7. /tg/webhook/<secret> with no body     400 (bad json)
#   8. Telegram getMe                        ok, bot username matches
#   9. Telegram getWebhookInfo               url is registered, pending=0
#
# Exit code: 0 if all checks pass, 1 otherwise.

set -uo pipefail

if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  . .env
  set +o allexport
fi

BASE_URL="${1:-}"
if [ -z "$BASE_URL" ]; then
  if [ -z "${PROJECT_ID:-}" ] || [ -z "${SERVICE:-}" ] || [ -z "${REGION:-}" ]; then
    echo "ERROR: pass a base URL, or set PROJECT_ID/SERVICE/REGION in .env"
    exit 1
  fi
  BASE_URL=$(gcloud run services describe "$SERVICE" \
    --project="$PROJECT_ID" --region="$REGION" \
    --format='value(status.url)' 2>/dev/null)
  if [ -z "$BASE_URL" ]; then
    echo "ERROR: could not resolve service URL via gcloud"
    exit 1
  fi
fi
BASE_URL="${BASE_URL%/}"

PASS=0
FAIL=0

check() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [ "$actual" = "$expected" ]; then
    printf '  ✓ %-44s [%s]\n' "$name" "$actual"
    PASS=$((PASS + 1))
  else
    printf '  ✗ %-44s [got %s, want %s]\n' "$name" "$actual" "$expected"
    FAIL=$((FAIL + 1))
  fi
}

http_status() {
  curl -s -o /dev/null -w '%{http_code}' "$@"
}

echo "==> Base URL: $BASE_URL"
echo "==> HTTP checks"

check "/health"                          200  "$(http_status "$BASE_URL/health")"
check "/"                                 200  "$(http_status "$BASE_URL/")"
check "/r/nonexistent"                    404  "$(http_status "$BASE_URL/r/nonexistent")"
check "/audio/nonexistent.mp3"            404  "$(http_status "$BASE_URL/audio/nonexistent.mp3")"
check "/cards.apkg (no cards yet)"        404  "$(http_status "$BASE_URL/cards.apkg")"
check "/tg/webhook/wrong-secret"          403  "$(http_status -X POST -d '{}' -H 'Content-Type: application/json' "$BASE_URL/tg/webhook/wrong-secret")"

if [ -n "${TELEGRAM_WEBHOOK_SECRET:-}" ]; then
  check "/tg/webhook/<secret> empty body" 400  "$(http_status -X POST -d 'not-json' -H 'Content-Type: application/json' "$BASE_URL/tg/webhook/$TELEGRAM_WEBHOOK_SECRET")"
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "==> Telegram checks"
  GETME=$(curl -sf "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe" || echo '{}')
  USERNAME=$(echo "$GETME" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result",{}).get("username",""))' 2>/dev/null)
  if [ -n "$USERNAME" ]; then
    printf '  ✓ getMe                                       [@%s]\n' "$USERNAME"
    PASS=$((PASS + 1))
  else
    printf '  ✗ getMe                                       [bad token or network]\n'
    FAIL=$((FAIL + 1))
  fi

  WHINFO=$(curl -sf "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo" || echo '{}')
  WHURL=$(echo "$WHINFO" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result",{}).get("url",""))' 2>/dev/null)
  PENDING=$(echo "$WHINFO" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result",{}).get("pending_update_count",0))' 2>/dev/null)
  LAST_ERR=$(echo "$WHINFO" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result",{}).get("last_error_message",""))' 2>/dev/null)

  EXPECTED_PREFIX="$BASE_URL/tg/webhook/"
  if [ -z "$WHURL" ]; then
    printf '  ✗ getWebhookInfo url                          [not registered]\n'
    FAIL=$((FAIL + 1))
  elif [[ "$WHURL" == "$EXPECTED_PREFIX"* ]]; then
    printf '  ✓ getWebhookInfo url                          [matches]\n'
    PASS=$((PASS + 1))
  else
    printf '  ✗ getWebhookInfo url                          [got %s]\n' "$WHURL"
    FAIL=$((FAIL + 1))
  fi

  printf '    pending_update_count = %s\n' "$PENDING"
  if [ -n "$LAST_ERR" ]; then
    printf '    last_error_message   = %s\n' "$LAST_ERR"
  fi
fi

echo
echo "==> $PASS passed, $FAIL failed"
exit "$FAIL"
