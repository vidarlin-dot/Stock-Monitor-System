#!/bin/bash
# Usage: ./trigger_daily.sh <workflow> [skip_exa]
#   workflow: daily_monitor | daily_taiwan_monitor
#
# Requires: STOCK_MONITOR_PAT env var (PAT with 'repo' scope)
#
# Example:
#   STOCK_MONITOR_PAT=ghp_xxx ./trigger_daily.sh daily_taiwan_monitor

REPO="vidarlin-dot/Stock-Monitor-System"
WORKFLOW="${1:?usage: trigger_daily.sh <workflow>}"

case "$WORKFLOW" in
  daily_taiwan_monitor) FILE="daily_taiwan_monitor.yml" ;;
  daily_monitor)        FILE="daily_monitor.yml" ;;
  *) echo "unknown workflow: $WORKFLOW"; exit 1 ;;
esac

curl -s -X POST \
  "https://api.github.com/repos/${REPO}/actions/workflows/${FILE}/dispatches" \
  -H "Authorization: token ${STOCK_MONITOR_PAT}" \
  -H "Content-Type: application/json" \
  -d '{"ref": "main"}'

if [ $? -eq 0 ]; then
  echo "OK: triggered ${FILE}"
else
  echo "Failed to trigger ${FILE}"
  exit 1
fi
