# ngrok 固定 Domain 穩定化規劃

## 目前 localtunnel 依賴

目前程式碼與設定中，實際寫死 localtunnel 的位置只有：

| 檔案 | 位置 | 現況 |
|------|------|------|
| `workflows/telegram_query_via_local_api.json` | `Local SQLite Query API` HTTP Request URL | 已改為優先讀取 `API_BASE_URL`，未設定時回退 `https://solid-results-remain.loca.lt/query` |
| `logs/localtunnel.log` | 執行紀錄 | 只作為歷史 log，不作為設定來源 |

`.env` 目前只有 `TELEGRAM_BOT_TOKEN` 與 `TELEGRAM_CHAT_ID`。v1.1 起建議補上 `PUBLIC_TUNNEL_PROVIDER`、`API_BASE_URL`、`LOCAL_API_PORT`、`NGROK_DOMAIN`。

## 切換後需要修改的位置

| 項目 | 建議值 |
|------|--------|
| `.env` | `PUBLIC_TUNNEL_PROVIDER=ngrok` |
| `.env` | `NGROK_DOMAIN=<你的固定 ngrok domain>` |
| `.env` | `API_BASE_URL=https://<你的固定 ngrok domain>` |
| n8n workflow | 匯入或更新 `workflows/telegram_query_via_local_api.json`，讓 HTTP Request 使用 `{{$env.API_BASE_URL}}` |
| Telegram Bot API | 若使用 polling，不需 webhook；若改 webhook，網址需指向固定 ngrok domain |

## ngrok 安裝

```bash
brew install ngrok/ngrok/ngrok
ngrok version
```

## login token 設定

在 ngrok dashboard 建立帳號並取得 authtoken 後執行：

```bash
ngrok config add-authtoken <NGROK_AUTHTOKEN>
```

## 固定 domain 設定

在 ngrok dashboard 建立 reserved/static domain，例如：

```text
your-static-domain.ngrok-free.app
```

更新 `land-intel-system/.env`：

```bash
PUBLIC_TUNNEL_PROVIDER=ngrok
LOCAL_API_PORT=5055
NGROK_DOMAIN=your-static-domain.ngrok-free.app
API_BASE_URL=https://your-static-domain.ngrok-free.app
```

## 啟動方式

先啟動本地查詢 API：

```bash
cd /Users/xiaomingyang/projects/land-intel-system
python3 scripts/telegram_query_api.py
```

另開一個終端啟動 ngrok：

```bash
cd /Users/xiaomingyang/projects/land-intel-system
bash scripts/start_public_tunnel.sh
```

## 測試方式

本地 API：

```bash
curl http://127.0.0.1:5055/health
curl -X POST http://127.0.0.1:5055/query \
  -H 'Content-Type: application/json' \
  -d '{"text":"大園區"}'
```

ngrok 公開網址：

```bash
curl https://your-static-domain.ngrok-free.app/health
curl -X POST https://your-static-domain.ngrok-free.app/query \
  -H 'Content-Type: application/json' \
  -d '{"text":"大園區"}'
```

n8n：

1. 確認 n8n 環境變數 `API_BASE_URL=https://your-static-domain.ngrok-free.app`。
2. 重啟 n8n，讓環境變數生效。
3. 在 `Local SQLite Query API` 節點執行測試。
4. 從 Telegram 發送「大園區」確認能收到查詢結果。

Telegram：

```bash
python3 - <<'PY'
import json, pathlib, urllib.request
env = {}
for line in pathlib.Path('.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()
token = env['TELEGRAM_BOT_TOKEN']
with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getMe') as r:
    print(json.loads(r.read())['ok'])
PY
```

## localtunnel 到 ngrok 的完整切換順序

1. 保持目前 localtunnel 與 n8n workflow 正常運作。
2. 安裝 ngrok 並設定 authtoken。
3. 在 ngrok dashboard 建立固定 domain。
4. 啟動本地 `telegram_query_api.py`。
5. 用 `bash scripts/start_public_tunnel.sh` 啟動 ngrok。
6. 用 curl 測試 ngrok `/health` 與 `/query`。
7. 設定 n8n 的 `API_BASE_URL` 為 ngrok 固定網址。
8. 重啟 n8n，確認 workflow 的 HTTP Request 節點讀到新網址。
9. 手動執行 n8n 節點測試。
10. 從 Telegram 實測查詢。
11. 觀察 n8n execution、`logs/telegram_query_api.log` 與 Telegram 回覆。
12. 確認穩定後，再停止 localtunnel。

## 回退 localtunnel

若 ngrok 失效：

1. 將 `.env` 改回：

```bash
PUBLIC_TUNNEL_PROVIDER=localtunnel
API_BASE_URL=https://solid-results-remain.loca.lt
LOCALTUNNEL_SUBDOMAIN=solid-results-remain
```

2. 啟動 localtunnel：

```bash
bash scripts/start_public_tunnel.sh
```

3. 將 n8n 的 `API_BASE_URL` 改回 localtunnel 網址並重啟 n8n。
4. 重新測試 n8n HTTP Request 節點與 Telegram 查詢。

## 風險

- n8n 必須能讀取 `API_BASE_URL` 環境變數；修改後需重啟 n8n。
- ngrok 免費方案的 domain/連線限制依帳號方案而定，需以 dashboard 顯示為準。
- 若本地 `telegram_query_api.py` 未啟動，ngrok domain 仍會回 502 或連線失敗。
- Telegram polling 模式不依賴 webhook；若未來改 webhook，切換網址時需同步更新 Telegram webhook。
