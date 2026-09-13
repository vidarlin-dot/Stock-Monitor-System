#!/bin/bash
# Start the LINE webhook server with environment variables
# Usage: ./start.sh [port]
#   port defaults to 5000

set -e

PORT="${1:-5000}"

if [ ! -f .env ]; then
    echo "❌ .env not found. Copy .env.example and fill in values:"
    echo "   cp .env.example .env"
    exit 1
fi

# shellcheck disable=SC1090
set -a
source .env
set +a

echo "🚀 Starting Taiwan Stock LINE Bot on port ${PORT}..."
echo "   Webhook URL: http://localhost:${PORT}/webhook/line"
echo ""
echo "   In a separate terminal, expose with ngrok:"
echo "   ngrok http ${PORT}"
echo ""

exec python -m src.line_bot_webhook
