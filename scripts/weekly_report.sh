#!/bin/bash
# weekly_report.sh — 老蕭 LAND 每週土地戰情報告
# 由 launchd 每週一 09:00 觸發，也可手動執行。

set -euo pipefail

PROJECT_DIR="/Users/xiaomingyang/projects/land-intel-system"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/weekly_report_$(date +%Y%m%d).log"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

{
  echo "======================================================"
  echo "  LAND 週報 開始：$(date '+%Y-%m-%d %H:%M:%S')"
  echo "======================================================"

  "$PYTHON" scripts/weekly_report.py

  echo ""
  echo "======================================================"
  echo "  LAND 週報 完成：$(date '+%Y-%m-%d %H:%M:%S')"
  echo "======================================================"

} 2>&1 | tee "$LOG_FILE"
