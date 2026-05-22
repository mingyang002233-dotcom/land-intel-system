#!/bin/bash
# Start the public tunnel for the local Telegram query API.
# Default provider is ngrok. localtunnel remains available as a fallback.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

PROVIDER="${PUBLIC_TUNNEL_PROVIDER:-ngrok}"
PORT="${LOCAL_API_PORT:-5055}"

case "$PROVIDER" in
  ngrok)
    if [ -z "${NGROK_DOMAIN:-}" ]; then
      echo "NGROK_DOMAIN is required when PUBLIC_TUNNEL_PROVIDER=ngrok"
      echo "Example: NGROK_DOMAIN=your-static-domain.ngrok-free.app"
      exit 1
    fi
    exec ngrok http --domain="$NGROK_DOMAIN" "$PORT"
    ;;
  localtunnel)
    SUBDOMAIN="${LOCALTUNNEL_SUBDOMAIN:-solid-results-remain}"
    exec npx localtunnel --port "$PORT" --subdomain "$SUBDOMAIN"
    ;;
  *)
    echo "Unsupported PUBLIC_TUNNEL_PROVIDER: $PROVIDER"
    echo "Use ngrok or localtunnel."
    exit 1
    ;;
esac
