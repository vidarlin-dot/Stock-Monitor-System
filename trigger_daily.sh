#!/bin/bash
# Usage: ./trigger_daily.sh <workflow> [skip_exa]
#   workflow: daily_monitor | daily_taiwan_monitor | monthly_update
#   skip_exa: true|false (default: false)
#
# Requires: GITHUB_PAT env var (PAT with 'repo' scope)
#
# Example:
#   GITHUB_PAT=ghp_xxx ./trigger_daily.sh daily_taiwan_monitor
#   GITHUB_PAT=ghp_xxx ./trigger_daily.sh daily_monitor true

REPO="vidarlin-dot/Stock-Monitor-System"
WORKFLOW="${1:?usage: trigger_daily.sh <workflow> [skip_exa]}"
SKIP_EXA="${2:-false}"

case "$WORKFLOW" in
  daily_taiwan_monitor) FILE="daily_taiwan_monitor.yml" ;;
  daily_monitor)        FILE="daily_monitor.yml" ;;
  monthly_update)       FILE="monthly_update.yml" ;;
  *) echo "unknown workflow: $WORKFLOW"; exit 1 ;;
esac

RESPONSE=$(curl -s -X POST \
  "https://api.github.com/repos/${REPO}/actions/workflows/${FILE}/dispatches" \
  -H "Authorization: token ${GITHUB_PAT}" \
  -H "Content-Type: application/json" \
  -d "{\"ref\": \"main\"}")

if [ $? -eq 0 ] && ! echo "$RESPONSE" | grep -q "error"; then
  echo "Triggered ${FILE} (skip_exa=${SKIP_EXA})"
else
  echo "Failed: $RESPONSE"
  exit 1
fi
