#!/bin/bash
# monthly_update.sh
# LAND 系統每月自動更新腳本。
# 由 launchd 在每月 2/12/22 號 08:30 觸發，也可手動執行。

set -euo pipefail

PROJECT_DIR="/Users/xiaomingyang/projects/land-intel-system"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/monthly_update_$(date +%Y%m%d).log"

# ── 進入專案目錄 ──────────────────────────────────────
cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

if [ "${LAND_FORCE_UPDATE:-0}" != "1" ]; then
  set +e
  "$PYTHON" scripts/monthly_update_guard.py --status
  GUARD_STATUS=$?
  set -e
  case "$GUARD_STATUS" in
    0)
      echo "[monthly_update] 本期已完成，跳過月更新。若要強制執行，設定 LAND_FORCE_UPDATE=1。"
      exit 0
      ;;
    2)
      echo "[monthly_update] 本期尚未完成，開始月更新。"
      ;;
    *)
      echo "[monthly_update] 補跑檢查失敗，停止執行。status=$GUARD_STATUS"
      exit "$GUARD_STATUS"
      ;;
  esac
fi

notify_failure() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "$PYTHON" scripts/notify_update.py --failure "$LOG_FILE" || true
  fi
  exit "$status"
}
trap notify_failure EXIT

# ── 執行並同時輸出到終端與 log ──────────────────────
{
  echo "======================================================"
  echo "  LAND 月更新 開始：$(date '+%Y-%m-%d %H:%M:%S')"
  echo "======================================================"

  # Step 1: 下載新期別
  echo ""
  echo "[Step 1] 下載最新增量期別"
  "$PYTHON" scripts/download_realprice.py --period auto
  echo ""

  # Step 2: 維護 pipeline
  echo "[Step 2] 維護 pipeline（parse + backfill + cleanup + validate）"
  "$PYTHON" scripts/maintenance_pipeline.py
  echo ""

  # Step 3: 更新覆蓋狀態紀錄
  echo "[Step 3] 檢查更新覆蓋狀態並寫入 update_history"
  "$PYTHON" scripts/check_update_schedule.py
  echo ""

  # Step 4: Telegram 通知
  echo "[Step 4] 發送 Telegram 更新通知"
  "$PYTHON" scripts/notify_update.py
  echo ""

  # Step 5: 591 土地情報收集 + 情報摘要推播
  echo "[Step 5] 591 土地情報收集"
  "$PYTHON" scripts/intel_591.py scrape
  "$PYTHON" scripts/intel_591.py snapshot
  echo ""

  echo "[Step 5b] 土地情報摘要推播"
  "$PYTHON" scripts/telegram_summary.py
  echo ""

  # Step 6: 實價雷達推播（僅 S/A 級）
  echo "[Step 6] 實價異常成交雷達推播"
  "$PYTHON" scripts/radar.py anomaly --push
  echo ""

  # Step 7: 只讀健康檢查（失敗只記錄，不中斷已完成流程）
  echo "[Step 7] 系統健康檢查"
  if python3 scripts/system_health.py; then
    echo "[健康檢查] OK"
  else
    echo "[健康檢查] FAIL（請查看上方 system_health.py 輸出）"
  fi
  echo ""

  # Step 8: 寫入本期成功標記，供開機/登入後補跑判斷使用
  echo "[Step 8] 寫入月更新成功標記"
  "$PYTHON" scripts/monthly_update_guard.py --mark-success
  echo ""

  echo "======================================================"
  echo "  LAND 月更新 完成：$(date '+%Y-%m-%d %H:%M:%S')"
  echo "======================================================"

} 2>&1 | tee "$LOG_FILE"
