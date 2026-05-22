# LAND 系統資料維護 SOP

## 系統定位

**土地成交情報查詢系統**
功能範圍：CSV 匯入 → SQLite 儲存 → 查詢 → Telegram 顯示
不包含：AI 分析、AI 推薦、異常偵測、自動推論

---

## 一、每次更新 CSV 後的標準流程

### 完整維護（一鍵）

```bash
cd /path/to/land-intel-system
python3 scripts/maintenance_pipeline.py
```

執行順序：
1. `parse_realprice.py` — 將 csv/ 目錄的 CSV 匯入 SQLite
2. `backfill_section_name.py` — 回填缺失的 section_name
3. `cleanup_orphan_land_details.py` — 清除孤立 land_details
4. `validate_land_data.py` — 資料品質驗證

任一步失敗會立即停止並顯示錯誤訊息。

---

## 二、各步驟單獨執行

```bash
# 1. 匯入 CSV
python3 scripts/parse_realprice.py

# 2. 回填 section_name（地段名稱）
python3 scripts/backfill_section_name.py

# 3. 清孤立 land_details
python3 scripts/cleanup_orphan_land_details.py

# 4. 資料品質驗證
python3 scripts/validate_land_data.py

# 4b. 驗證同時搜尋特定地段是否存在
python3 scripts/validate_land_data.py 貴仁段 中運段
```

---

## 三、查詢測試

```bash
# 基本查詢
python3 scripts/query_land.py "中運段 近半年"

# 指定縣市區段
python3 scripts/query_land.py "桃園市 中壢區 中運段 近半年"

# 農地查詢
python3 scripts/query_land.py "大園農地 近半年"
```

---

## 四、Telegram Bot 操作

### 啟動

```bash
cd /path/to/land-intel-system
python3 scripts/telegram_query_bot.py
```

### 背景執行（長期運行）

```bash
nohup python3 scripts/telegram_query_bot.py > logs/bot.log 2>&1 &
echo "Bot PID: $!"
```

### 停止

```bash
# 查找 PID
ps aux | grep telegram_query_bot | grep -v grep

# 停止
kill <PID>
```

### 確認 Bot 狀態

```bash
# 測試 token 是否有效
python3 -c "
import urllib.request, json, os
token = os.environ.get('TELEGRAM_BOT_TOKEN') or open('.env').read().split('TELEGRAM_BOT_TOKEN=')[1].split()[0]
with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getMe') as r:
    print(json.loads(r.read())['result']['username'])
"
```

---

## 五、常見錯誤處理

### DB not found

```
FileNotFoundError: DB not found: .../db/land_intel.db
```

→ 先執行 `python3 scripts/init_db.py` 初始化資料庫

### CSV 匯入失敗

```
Error parsing CSV: ...
```

→ 確認 `csv/` 目錄內有正確格式的 CSV 檔案
→ CSV 檔名須符合 `{code}_lvr_land_*.csv` 格式

### section_name 缺失率過高

→ 執行 `python3 scripts/backfill_section_name.py`
→ 若仍缺失：確認 location_raw 欄位是否含「地號」字樣

### 重複資料過多

→ 同一批 CSV 被重複匯入
→ 解決：重建 DB 後重新匯入，或使用 UPSERT 邏輯（需修改 parse_realprice.py）

### Telegram 推播失敗

```
HTTPError: 401
```

→ 檢查 `.env` 內 `TELEGRAM_BOT_TOKEN` 是否正確

```
HTTPError: 403
```

→ 使用者尚未對 Bot 發送過訊息，需先在 Telegram 開啟對話

---

## 六、資料維護注意事項

### 不要手動修改 DB

- 不要用 DB Browser 直接修改 `land_intel.db`
- 所有修改應通過腳本進行，保留可重現性

### 備份 DB

```bash
cp db/land_intel.db db/land_intel_backup_$(date +%Y%m%d).db
```

### CSV 存放規則

- 所有 CSV 放入 `csv/` 目錄
- 不同批次的 CSV 若重複匯入會產生重複資料
- 建議每次更新前確認 CSV 是否為新批次

### section_name 規則

- 只有 `location_raw` 含「地號」才會 parse section_name
- 門牌地址（含路/街/巷/弄/號）section_name 保持 NULL（正確行為）
- PUA 字元（亂碼）的地段名保留原始值，不自動猜測

---

## 七、資料品質標準（參考值）

| 指標 | 正常範圍 | 說明 |
|------|---------|------|
| Parse 成功率 | 100% | 含地號的 location_raw 應全數解析 |
| 重複組數 | 0 | 每次維護後應清零 |
| 門牌誤判 | 0 | section_name 不應含路/街/大道路名 |
| 縣市前綴污染 | 0 | section_name 不應含縣市區前綴 |
| 異常日期 | 0 | trade_date 應在 1990~2100 之間 |
| section_name NULL % | ~87% | 正常，大部分為門牌地址 |

---

## 八、地段查不到的處理

若使用者回報某地段查不到（如「貴仁段」）：

1. 先用驗證腳本確認 DB 內是否存在：
   ```bash
   python3 scripts/validate_land_data.py 貴仁段
   ```

2. 若 DB 無資料 → 該地段不在已匯入的 CSV 中
   → 需要補充該縣市的 CSV 資料

3. 若 DB 有資料但查詢無結果 → 查詢邏輯問題
   → 檢查 `query_land.py` 的 `find_section_name()` 是否能正確解析

---

*最後更新：2026-05-15*
