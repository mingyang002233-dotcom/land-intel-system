#!/bin/bash
# test_n8n_workflow.sh — n8n workflow 自動測試腳本
# 用法：bash scripts/test_n8n_workflow.sh

set -uo pipefail

N8N_URL="http://localhost:5678"
N8N_BIN="$HOME/.npm/_npx/a8a7eec953f1f314/node_modules/.bin/n8n"
WORKSPACE="$HOME/.openclaw/workspace"
COOKIE_JAR="/tmp/n8n_test_cookies.txt"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

PASS=0
FAIL=0
WARN=0

log_pass() { echo "  ✅ $1"; ((PASS++)); }
log_fail() { echo "  ❌ $1"; ((FAIL++)); }
log_warn() { echo "  ⚠️  $1"; ((WARN++)); }
log_info() { echo "  ℹ️  $1"; }
section()  { echo ""; echo "▶ $1"; }

# ── 載入 .env ────────────────────────────────────────────
load_env() {
  for f in "$PROJECT_DIR/.env" "$PROJECT_DIR/../.env"; do
    if [[ -f "$f" ]]; then
      while IFS='=' read -r k v; do
        [[ "$k" =~ ^# ]] && continue
        [[ -z "$k" ]] && continue
        v="${v%\"}"
        v="${v#\"}"
        v="${v%\'}"
        v="${v#\'}"
        export "$k=$v"
      done < <(grep -v '^#' "$f" | grep '=')
    fi
  done
}
load_env

# ────────────────────────────────────────────────────────
# 1. 檢查 n8n 是否啟動
# ────────────────────────────────────────────────────────
section "1. 檢查 n8n 狀態"

HEALTH=$(curl -s --max-time 5 "$N8N_URL/healthz" 2>/dev/null)
if echo "$HEALTH" | grep -q '"ok"'; then
  log_pass "n8n 已啟動 ($N8N_URL)"
else
  log_warn "n8n 未啟動，嘗試啟動..."
  if [[ ! -f "$N8N_BIN" ]]; then
    log_fail "找不到 n8n 執行檔：$N8N_BIN"
    echo ""
    echo "=== 總結：n8n 未安裝，無法繼續測試 ==="
    exit 1
  fi
  N8N_USER_FOLDER=~/.n8n N8N_PORT=5678 "$N8N_BIN" start > /tmp/n8n_autostart.log 2>&1 &
  echo "  等待 n8n 啟動(最多 15 秒)..."
  for i in {1..15}; do
    sleep 1
    HEALTH=$(curl -s --max-time 3 "$N8N_URL/healthz" 2>/dev/null)
    if echo "$HEALTH" | grep -q '"ok"'; then
      log_pass "n8n 已成功啟動"
      break
    fi
    if [[ $i -eq 15 ]]; then
      log_fail "n8n 啟動失敗，請查看 /tmp/n8n_autostart.log"
      exit 1
    fi
  done
fi

# ────────────────────────────────────────────────────────
# 2. 登入並檢查 workflow 是否存在
# ────────────────────────────────────────────────────────
section "2. 登入 n8n + 匯入 workflow"

EMAIL="${N8N_EMAIL:-mingyang002233@gmail.com}"
PASSWORD="${N8N_PASSWORD:-LandIntel2026!}"

LOGIN_RESP=$(curl -s -c "$COOKIE_JAR" \
  -X POST "$N8N_URL/rest/login" \
  -H "Content-Type: application/json" \
  -d "{\"emailOrLdapLoginId\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" 2>/dev/null)

if echo "$LOGIN_RESP" | grep -q '"isOwner":true'; then
  log_pass "登入成功($EMAIL)"
else
  log_fail "登入失敗 → 請確認帳號密碼正確，或手動在瀏覽器開啟 $N8N_URL"
  echo "  回應：$(echo "$LOGIN_RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("message","?"))' 2>/dev/null)"
fi

# 取得現有 workflow 清單(用 n8n CLI export 列出)
EXISTING_WORKFLOWS=$(N8N_USER_FOLDER=~/.n8n "$N8N_BIN" list:workflow 2>/dev/null || echo "")
log_info "現有 workflow 數：$(echo "$EXISTING_WORKFLOWS" | grep -c 'id' || echo 0)"

# 匯入三個 workflow(CLI，不需 API key)
IMPORT_FILES=(
  "$WORKSPACE/n8n-航空城新聞推播-完整workflow.json"
  "$WORKSPACE/n8n-台灣土地實價登錄查詢系統-最佳化版.json"
  "$WORKSPACE/n8n-台灣土地實價登錄查詢系統-單一CSV版.json"
)

for wf in "${IMPORT_FILES[@]}"; do
  name=$(basename "$wf")
  if [[ ! -f "$wf" ]]; then
    log_fail "找不到 workflow 檔案：$name"
    continue
  fi
  IMPORT_OUT=$(N8N_USER_FOLDER=~/.n8n "$N8N_BIN" import:workflow --input="$wf" 2>&1)
  if echo "$IMPORT_OUT" | grep -qi "imported\|success\|successfully"; then
    log_pass "匯入：$name"
  elif echo "$IMPORT_OUT" | grep -qi "already\|exist\|duplicate"; then
    log_warn "已存在(跳過)：$name"
  else
    log_warn "匯入結果不明：$name"
    log_info "  → $IMPORT_OUT"
  fi
done

# 列出匯入後的 workflow
section "  匯入後 workflow 清單"
N8N_USER_FOLDER=~/.n8n "$N8N_BIN" list:workflow 2>/dev/null | while read -r line; do
  log_info "$line"
done

# ────────────────────────────────────────────────────────
# 3. 測試 Webhook(實價登錄查詢)
# ────────────────────────────────────────────────────────
section "3. 測試 Webhook(實價登錄查詢)"

# Webhook 路徑：/webhook-test/land-search(測試模式)或 /webhook/land-search(正式)
WEBHOOK_TEST_URL="$N8N_URL/webhook-test/land-search?city=桃園市&keyword=大竹"
WEBHOOK_PROD_URL="$N8N_URL/webhook/land-search?city=桃園市&keyword=大竹"

log_info "呼叫測試 webhook(等待最多 30 秒)..."
WH_RESP=$(curl -s --max-time 30 \
  -G "$N8N_URL/webhook-test/land-search" \
  --data-urlencode "city=桃園市" \
  --data-urlencode "keyword=大竹" 2>/dev/null)

if echo "$WH_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('total=',d.get('total','?'))" 2>/dev/null; then
  log_pass "Webhook 回應正常"
else
  log_warn "Webhook 測試模式未回應(workflow 可能需要先在 UI 開啟測試模式)"
  log_info "  嘗試正式 webhook..."
  WH_RESP2=$(curl -s --max-time 30 \
    -G "$N8N_URL/webhook/land-search" \
    --data-urlencode "city=桃園市" \
    --data-urlencode "keyword=大竹" 2>/dev/null)
  if echo "$WH_RESP2" | python3 -c "import sys,json; d=json.load(sys.stdin); print('total=',d.get('total','?'))" 2>/dev/null; then
    log_pass "正式 Webhook 回應正常"
  else
    log_warn "Webhook 未回應(workflow 尚未啟用，請在 n8n UI 手動 activate)"
    log_info "  回應：${WH_RESP2:0:200}"
  fi
fi

# ────────────────────────────────────────────────────────
# 4. 測試 Telegram 推播(用本地 Python 腳本)
# ────────────────────────────────────────────────────────
section "4. 測試 Telegram 推播"

PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}"

if [[ -z "$BOT_TOKEN" || -z "$CHAT_ID" ]]; then
  log_fail "未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID(請檢查 .env)"
else
  log_info "Bot token：${BOT_TOKEN:0:10}..."
  log_info "Chat ID  ：$CHAT_ID"

  # 發送測試訊息
  TG_RESP=$(curl -s --max-time 10 \
    -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d "chat_id=${CHAT_ID}&text=🧪+n8n+workflow+測試訊息｜$(date '+%Y-%m-%d+%H:%M')" 2>/dev/null)

  if echo "$TG_RESP" | grep -q '"ok":true'; then
    log_pass "Telegram 推播成功"
  else
    log_fail "Telegram 推播失敗"
    log_info "  回應：$(echo "$TG_RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("description","?"))' 2>/dev/null)"
  fi
fi

# 測試本地 telegram_summary.py(dry run)
if [[ -f "$PROJECT_DIR/scripts/telegram_summary.py" ]]; then
  log_info "執行 telegram_summary.py --dry..."
  SUMMARY_OUT=$("$PYTHON" "$PROJECT_DIR/scripts/telegram_summary.py" --dry 2>&1 | head -10)
  if echo "$SUMMARY_OUT" | grep -q "LAND\|土地\|情報\|無"; then
    log_pass "telegram_summary.py --dry 正常"
    log_info "  $(echo "$SUMMARY_OUT" | head -3)"
  else
    log_warn "telegram_summary.py 輸出異常"
    log_info "  $SUMMARY_OUT"
  fi
fi

# ────────────────────────────────────────────────────────
# 5. 總結報告
# ────────────────────────────────────────────────────────
section "5. 測試總結"

TOTAL=$((PASS + FAIL + WARN))
echo ""
echo "┌─────────────────────────────────────┐"
echo "│  n8n Workflow 測試報告               │"
echo "├─────────────────────────────────────┤"
printf "│  ✅ PASS : %-3d                       │\n" "$PASS"
printf "│  ❌ FAIL : %-3d                       │\n" "$FAIL"
printf "│  ⚠️  WARN : %-3d                       │\n" "$WARN"
echo "├─────────────────────────────────────┤"
if [[ $FAIL -eq 0 ]]; then
  echo "│  結論：全部通過，可正式使用           │"
else
  echo "│  結論：有項目失敗，請確認後再啟用     │"
fi
echo "└─────────────────────────────────────┘"
echo ""
echo "n8n UI：$N8N_URL"
echo "  → 在瀏覽器開啟，手動 Activate workflow 後 webhook 才會生效"
echo ""

exit $FAIL
