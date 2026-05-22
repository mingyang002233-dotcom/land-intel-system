# 老蕭 LAND 土地成交情報資料庫系統

## 目標

建立一套使用 Python + SQLite 的土地成交情報資料庫系統，專注於：

- 近半年實價登錄土地成交資料回補
- 土地成交 CSV 解析與過濾
- SQLite 土地成交資料庫為主存儲
- 自然語言快速查詢
- 補跑與更新機制設計

> 第一階段先完成架構規劃與資料模型，再逐步實作。避免一次做太大。

## 專案資料夾結構

- `land-intel-system/`
  - `data/`：下載後的 ZIP 或 CSV 原始資料存放位置（暫時保留，後續可歸檔或刪除）。
  - `csv/`：手動放置或自動下載的實價登錄 CSV 檔案。
  - `db/`：SQLite 資料庫檔案，例如 `land_intel.db`。
  - `scripts/`：Python 腳本架構。
  - `config/`：資料來源設定、城市/類型清單、排程與過濾規則。
  - `output/`：查詢報表、匯出 CSV、測試結果。
  - `logs/`：執行日誌、補跑紀錄、錯誤紀錄。

## 架構規劃

### 1. 資料來源層

- 內政部實價登錄 Open Data（CSV）：
  - 主要來源為「交易案件」資料，固定格式 CSV。
  - 第一版允許手動下載後放到 `csv/`。
  - 後續設計可加上 `scripts/download_realprice.py` 下載最近 2~3 季資料。

### 2. 解析與過濾層

- `scripts/parse_realprice.py` 負責：
  - 讀取 `csv/` 內 CSV 檔案。
  - 解析實價登錄欄位。
  - 篩選指定縣市與交易標的為「土地」。
  - 過濾不符合條件的交易、指定保留/排除類型。
  - 寫入 SQLite。
  - 避免重複匯入。

### 3. 資料庫層

- 使用 SQLite 作為主資料庫。
- 資料庫放在 `db/land_intel.db`（或可指定）。
- 只保留最重要的土地成交資料，CSV 為暫存。
- 以 `city + district + location_raw + trade_date + area_sqm + total_price` 作為唯一識別。

### 4. 查詢層

- `scripts/query_land.py` 支援自然語言查詢：
  - 自動解析縣市、行政區、時間範圍。
  - 支援語句如「新北市 新莊區 近半年」、「台中市 全區 今年」等。
  - 若輸入「全區」，則不限制行政區。
- 查詢結果輸出關鍵欄位：
  - 地段地號
  - 交易日期
  - 坪數
  - 單價
  - 總價
  - 使用分區
  - 備註
  - 是否值得調謄本

### 5. 補跑與更新層

- `scripts/update_realprice.py` 負責：
  - 檢查 SQLite 最後更新日期。
  - 檢查 `logs/` 最後成功執行紀錄。
  - 判斷是否漏掉內政部官方資料發布日（每月 1 / 11 / 21）後的系統執行窗口。
  - 判斷是否缺少近半年資料。
  - 產生補跑任務或提示需要補下載。
- 第一階段先實作檢查與紀錄邏輯，後續再接 macOS `launchd` 自動排程。

## SQLite Schema

### table: `land_transactions`

- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `city` TEXT NOT NULL
- `district` TEXT
- `section_name` TEXT
- `land_number` TEXT
- `location_raw` TEXT NOT NULL
- `trade_date` TEXT NOT NULL
- `area_sqm` REAL
- `area_ping` REAL
- `total_price` INTEGER
- `total_price_wan` REAL
- `unit_price_per_sqm` REAL
- `unit_price_per_ping_wan` REAL
- `land_use_zone` TEXT
- `land_use_type` TEXT
- `transaction_type` TEXT
- `transaction_target` TEXT
- `note` TEXT
- `source_file` TEXT
- `created_at` TEXT DEFAULT (datetime('now','localtime'))
- `unique_key` TEXT UNIQUE

### 其他 metadata table

- `import_logs`
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `source_file` TEXT
  - `imported_at` TEXT
  - `records_added` INTEGER
  - `records_skipped` INTEGER
  - `status` TEXT
  - `message` TEXT

- `update_history`
  - `id` INTEGER PRIMARY KEY AUTOINCREMENT
  - `checked_at` TEXT
  - `last_trade_date` TEXT
  - `coverage_start` TEXT
  - `coverage_end` TEXT
  - `missing_intervals` TEXT
  - `action_required` TEXT

## 資料來源與下載策略

### 資料來源

- 使用內政部實價登錄 Open Data：
  - 主要以 `schema-main.csv` 及 `schema-land.csv` 進行欄位確認。
  - 重要來源欄位包括：
    - `鄉鎮市區`
    - `交易標的`
    - `土地位置建物門牌`
    - `土地移轉總面積平方公尺`
    - `都市土地使用分區`
    - `非都市土地使用分區`
    - `交易年月日`
    - `總價元`
    - `單價元平方公尺`
    - `備註`
  - 目標為最近 2~3 季資料，用於回補近半年資料。
  - 依照交易日期，再篩選近半年。

### 下載策略（第一版）

- 先採手動下載 CSV：
  - 使用者將 CSV 檔放入 `land-intel-system/csv/`。
  - `scripts/parse_realprice.py` 讀取這些檔案並處理。
- 不保留大量 CSV：
  - 讀取完後可依策略刪除或移到 `data/`。
  - SQLite 是主資料庫。

### 自動下載設計

- `scripts/download_realprice.py` 現在包含手動匯入說明與未來自動下載架構。
- 未來可延伸：
  - 透過官方 Open Data 檔案列表下載最近三季或四季 ZIP/CSV。
  - 依據官方資料發布日（每月 1 / 11 / 21）與系統執行日（每月 2 / 12 / 22 08:30）補跑缺失資料。
  - 下載後直接解析並寫入 SQLite。
- 官方 Open Data 範例下載點為：
  - `https://plvr.land.moi.gov.tw/Download?type=zip&fileName=lvr_land.zip`
  - `schema-main.csv` / `schema-land.csv` 來自同一下載來源。

## 補跑邏輯說明

### 補跑需求

- 目標是確保近半年資料完整，避免漏跑。
- 主要檢查：
  1. SQLite 最後匯入的交易日期。
  2. `logs/` 或 `import_logs` 的最後成功匯入紀錄。
  3. 是否漏掉內政部官方資料發布日（每月 1 / 11 / 21）後的老蕭 LAND 系統執行日（每月 2 / 12 / 22 08:30）。
  4. 是否缺少近半年資料。

## 第一階段執行步驟

1. 初始化 SQLite：

```bash
python3 land-intel-system/scripts/init_db.py
```

2. 手動下載內政部實價登錄 CSV 到 `land-intel-system/csv/`。

3. 解析並匯入資料：

```bash
python3 land-intel-system/scripts/parse_realprice.py
```

4. 自然語言查詢：

```bash
python3 land-intel-system/scripts/query_land.py "新北市 新莊區 近半年"
```

5. 檢查補跑狀態：

```bash
python3 land-intel-system/scripts/update_realprice.py
```

  4. 是否缺少最近 6 個月資料範圍。

### 補跑設計

1. `update_realprice.py` 讀取 `db/land_intel.db` metadata 或查詢 `land_transactions` 最新交易日期。
2. 根據今天日期與已匯入資料，計算應該覆蓋的月份範圍。
3. 若發現缺口，列出缺失月份/季資料。
4. 若 `logs/` 顯示上次成功執行過久，觸發補跑檢查。
5. `monthly_update.sh` 在排程或登入後觸發，先檢查本期是否已成功；未成功才補跑一次。

### 補跑策略

- 先以近半年資料為基準。
- 如果缺少特定月份資料，優先補下載該月份或季的 CSV。
- 補跑時只將新筆或不存在的交易寫入 SQLite。
- 若同一筆已存在，跳過不重複匯入。
- Mac 不要求 24 小時運行；錯過 2 / 12 / 22 08:30 時，開機/登入後由 LaunchAgent 觸發補跑檢查。
- 本期成功後會寫入 `update_history.action_required = monthly_update_success:YYYY-MM-DD`，避免同一窗口重複執行。

## 下一步

1. 先建立 `land-intel-system/config/` 內的資料源與過濾規則。
2. 再設計 `scripts/parse_realprice.py` 以支援 CSV 解析與 SQLite 匯入。
3. 接著建 `scripts/query_land.py` 的自然語言查詢功能。
4. 最後補上 `scripts/update_realprice.py` 的檢查與補跑邏輯。
