#!/bin/bash
# LAND project entrypoint.

set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
START_TS="$(date +%s)"
START_TEXT="$(date '+%Y-%m-%d %H:%M:%S')"

echo "======================================================"
echo "  LAND run 開始：$START_TEXT"
echo "======================================================"

cd "$PROJECT_DIR" || exit 1

if bash scripts/monthly_update.sh; then
  STATUS=0
else
  STATUS=$?
  echo ""
  echo "❌ LAND run 失敗：scripts/monthly_update.sh exit $STATUS"
fi

END_TS="$(date +%s)"
END_TEXT="$(date '+%Y-%m-%d %H:%M:%S')"
ELAPSED=$((END_TS - START_TS))

echo "======================================================"
echo "  LAND run 結束：$END_TEXT"
echo "  總耗時：${ELAPSED} 秒"
echo "======================================================"

exit "$STATUS"
