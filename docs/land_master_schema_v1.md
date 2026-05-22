# 老蕭 LAND 土地主資料 Schema v1

> 定版日：2026-05-22　最後更新：2026-05-22（v4.2 郵遞區號補正）
> 定位：地主＋土地異動追蹤系統（非單純土地名單）

---

## 系統架構定位

```
Excel 乾淨版主清冊（人工維護）
  ↓ Python normalize / import
SQLite（正式資料庫）
  ↓ Python 實價比對
實價提醒報表（獨立 Excel，不寫回主清冊）
  ↓ Telegram 摘要推播
手機確認 → 人工調閱謄本
  ↓ Telegram bot 快速回填（/note /phone /sold）
SQLite 更新
```

- **Excel 主清冊** = 人工維護，24 個核心欄位，無外部連結，無舊公式
- **SQLite** = 正式資料庫，含程式自動生成欄位，為查詢與比對主體
- **實價提醒報表** = Python 自動輸出獨立 Excel，**不寫回主清冊**
- **Python** = 負責 normalize、匯入、實價比對、報表輸出
- **Telegram** = 手機端摘要推播 + 快速回填（聯絡紀錄、狀態更新）

### 正式 import 來源（唯一）

```
/Users/xiaomingyang/Desktop/excel土地資料維護/土地主清冊_正式版_20260522_郵遞區號補正版.xlsx
```

> **import_land_master.py 預設讀取此檔。不要改回舊路徑。**

**此版本特性：**
- 148,265 列 × 24 欄，無 externalLinks，無修復提示
- 郵遞區號已補正 7,234 筆（信心高，依地址自動補）
- 補正紀錄：`郵遞區號補正紀錄_20260522.xlsx`

### 歷史備份檔案（僅保留，不再作為 import 來源）

| 檔案 | 說明 |
|------|------|
| `土地資料維護.xlsx` | 原始檔，含舊公式與 externalLinks，僅作原始備份 |
| `土地資料維護_backup_*.xlsx` | 系統備份，不再修改 |
| `土地主清冊_乾淨版_20260522.xlsx` | 郵遞區號補正前的乾淨版，保留供回溯 |
| `土地主清冊_正式版_20260522.xlsx` | 郵遞區號補正前的格式版，保留供回溯 |
| `cleaned_preview.xlsx`（如存在） | 暫存預覽檔，不作為 import 來源 |

**主清冊設計原則：**
1. 只保留 24 個核心欄位（見第一節）
2. 不含 externalLinks，不含舊公式，開啟無修復提示
3. 不直接寫入實價提醒欄位
4. 實價提醒一律輸出獨立報表（`實價提醒報表_YYYYMMDD_HHMMSS.xlsx`）
5. import_land_master.py 預設讀取郵遞區號補正版（見上方路徑）
6. 所有権人空白／疑似錯誤：等新謄本／電傳，不自動補

### 檔案命名規則

| 類型 | 格式 | 說明 |
|------|------|------|
| 正式 import 來源 | `土地主清冊_正式版_YYYYMMDD_郵遞區號補正版.xlsx` | 每次補正後另存新版 |
| 實價提醒報表 | `實價提醒報表_YYYYMMDD_HHMMSS.xlsx` | `realprice_alert.py` 自動輸出 |
| 資料品質報表 | `資料品質檢查報表_YYYYMMDD_HHMMSS.xlsx` | `check_land_master_quality.py` 輸出 |
| 補正紀錄 | `郵遞區號補正紀錄_YYYYMMDD.xlsx` | `apply_postal_fix.py` 輸出 |
| 備份 | `土地資料維護_backup_*.xlsx` | 保留，不修改 |

---

## 一、保留欄位（人工維護）

以下欄位維持在主 Excel 中人工填寫。

| # | 欄位名稱 | 說明 | 備註 |
|---|---------|------|------|
| 1 | 更新日期 | 最後系統或人工更新日 | 匯入時自動更新 |
| 2 | 分區 | 使用分區，如：建地、農地、工業地 | |
| 3 | 位置 | 戰區／開發區分類，如：航空城、市府二期、安置街廓 | 高價值識別欄 |
| 4 | 縣市 | 行政區縣市 | |
| 5 | 鄉鎮市區 | 行政區鄉鎮市區（原「地區」欄） | |
| 6 | 地段 | 地段名稱，保留原始人工格式 | normalize 交由 Python |
| 7 | 地號 | 地號，保留原始人工格式（如：26-3） | normalize 交由 Python |
| 8 | 公告現值 | 公告現值（元/坪） | |
| 9 | 次序 | 登記次序，用於判斷同筆土地所有權異動順序 | |
| 10 | 登記日期 | 所有權登記日期 | |
| 11 | 原因發生日期 | 買賣／繼承等原因發生日（非登記日） | |
| 12 | 登記原因 | 買賣、繼承、贈與等 | |
| 13 | 所有權人 | 地主姓名 | |
| 14 | 統一編號（遮罩） | 傳真／列印用，格式如：H122\*\*\*\*\*1 | 資安遮罩 |
| 15 | 統一編號（完整） | 小白機比對後填入，作為地主唯一識別 | 嚴格存取控制 |
| 16 | 郵遞區號 | Word 合併列印寄信用 | |
| 17 | 地址 | 通訊地址，用於寄信、找地主、開發聯繫 | |
| 18 | 已售出 | 地主已變現標記；不代表資料失效，仍可作未來客戶追蹤 | |
| 19 | 分母 | 持分分母 | |
| 20 | 分子 | 持分分子 | |
| 21 | 土地總坪 | 整筆土地總坪數 | |
| 22 | 權利範圍 | 實際持有坪數說明；建議未來改名為「實際持有坪數」 | |
| 23 | 備註 | 長期重要資訊（位置特徵、開發狀況、特殊限制等） | 不放通話流水 |
| 24 | 電話 | 本人、家屬、代理人、小白機查得電話，暫不拆欄 | |

> **「小段」欄**：目前資料中存在，視資料完整度決定是否保留，不強制刪除。

---

## 二、淘汰欄位

以下欄位從主 Excel 移除，理由如下。

| 欄位名稱 | 淘汰原因 |
|---------|---------|
| 資料清理後的地段 | Excel workaround；改由 Python `normalized_section` 自動生成 |
| 統一格式的地號 | Excel workaround；改由 Python `normalized_land_no` 自動生成 |
| 地目 | 主 Excel 不再維護；原始備份可保留，未來由地籍系統比對補充 |
| 公式代入郵遞區號 | Excel VLOOKUP workaround；改由程式或 lookup table 生成 |
| 時價登入異動 | 僅含「查無資料」字串；改由正式實價比對系統（`realprice_match_status`）產生 |
| 地址錯誤提醒 | 欄位完全空白，無任何填寫紀錄，直接移除 |
| 統一編號（重複欄） | 與遮罩版重複，且暴露完整身分資料，移除 |

---

## 三、Python 自動生成欄位

以下欄位**不放進主 Excel**，由 Python 在匯入 SQLite 時計算並寫入。

| 欄位名稱 | 說明 | 生成時機 |
|---------|------|---------|
| `normalized_section` | 地段標準化名稱（去掉括號代碼，如：`普義段(0835)` → `普義段`） | 匯入時 |
| `normalized_land_no` | 地號標準化（`26-3` → `0026-0003`） | 匯入時 |
| `land_match_key` | 地段＋地號組成的比對 key，用於跨資料來源比對 | 匯入時 |
| `owner_key` | 統一編號完整版的 hash，作為地主唯一識別（不儲存明文） | 匯入時 |
| `actual_owned_area` | 土地總坪 × (分子／分母)，計算實際持有坪數 | 匯入時 |
| `realprice_match_status` | 實價登錄比對狀態：`matched` / `no_data` / `pending` | 月更新時 |
| `last_realprice_check_at` | 最近一次實價比對時間 | 月更新時 |
| `telegram_last_note_at` | Telegram 最後一次回填備注的時間 | Telegram 回填時 |

---

## 四、SQLite 建議欄位設計

```sql
CREATE TABLE land_master (
    -- 主鍵
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 人工維護欄位（對應 Excel）
    updated_at              TEXT,           -- 更新日期
    zone_type               TEXT,           -- 分區（建地/農地/工業地）
    location_tag            TEXT,           -- 位置（戰區分類）
    city                    TEXT,           -- 縣市
    district                TEXT,           -- 鄉鎮市區
    section_raw             TEXT,           -- 地段（原始）
    land_no_raw             TEXT,           -- 地號（原始）
    announced_value         INTEGER,        -- 公告現值（元/坪）
    reg_seq                 INTEGER,        -- 次序
    reg_date                TEXT,           -- 登記日期
    cause_date              TEXT,           -- 原因發生日期
    reg_reason              TEXT,           -- 登記原因
    owner_name              TEXT,           -- 所有權人
    owner_id_masked         TEXT,           -- 統一編號（遮罩）
    owner_id_full           TEXT,           -- 統一編號（完整，加密儲存）
    postal_code             TEXT,           -- 郵遞區號
    address                 TEXT,           -- 地址
    is_sold                 INTEGER DEFAULT 0,  -- 已售出（0/1）
    share_denom             INTEGER,        -- 分母
    share_numer             INTEGER,        -- 分子
    total_area_ping         REAL,           -- 土地總坪
    ownership_range         TEXT,           -- 權利範圍說明
    note                    TEXT,           -- 備註
    phone                   TEXT,           -- 電話

    -- Python 自動生成欄位
    normalized_section      TEXT,           -- 標準化地段
    normalized_land_no      TEXT,           -- 標準化地號
    land_match_key          TEXT,           -- 地段+地號比對 key
    owner_key               TEXT,           -- 地主唯一識別 hash
    actual_owned_area       REAL,           -- 實際持有坪數（計算值）
    realprice_match_status  TEXT DEFAULT 'pending',  -- 實價比對狀態
    last_realprice_check_at TEXT,           -- 最近實價比對時間
    telegram_last_note_at   TEXT,           -- Telegram 最後回填時間

    -- 系統欄位
    imported_at             TEXT DEFAULT (datetime('now')),
    source_row              INTEGER         -- 對應 Excel 原始行號，便於回溯
);

-- 查詢常用索引
CREATE INDEX idx_lm_city_district ON land_master(city, district);
CREATE INDEX idx_lm_land_match_key ON land_master(land_match_key);
CREATE INDEX idx_lm_owner_key ON land_master(owner_key);
CREATE INDEX idx_lm_is_sold ON land_master(is_sold);
CREATE INDEX idx_lm_location_tag ON land_master(location_tag);
```

---

## 五、Telegram 快速回填目標欄位

手機端 Telegram bot 回填時，只更新以下欄位：

| 欄位 | 說明 |
|------|------|
| `phone` | 新增或更新電話號碼 |
| `note` | 追加備注（不覆蓋，append 模式） |
| `is_sold` | 標記已售出 |
| `updated_at` | 自動更新為回填時間 |
| `telegram_last_note_at` | 自動記錄回填時間戳 |

> 回填設計原則：只追加、不刪除；`note` 欄採 append，保留歷史記錄。

---

## 六、後續演進節點

| 階段 | 工作 | 狀態 |
|------|------|------|
| v1 | Schema 定版，文件化 | ✅ 完成 |
| v2 | Python import script：Excel → SQLite（`import_land_master.py`） | ✅ 完成 |
| v2.1 | 效能優化（86× 加速，pre-load set + executemany + WAL）| ✅ 完成 |
| v2.2 | row_hash SKIP 機制（避免重複更新未變更資料）| ✅ 完成 |
| v3 | Telegram 快速回填 bot（`land_master_bot.py`）| ✅ 完成 |
| v3.1 | Telegram 查詢指令（`/query` `/history` `/owner`）| ✅ 完成 |
| v4 | 實價登錄比對 + 獨立提醒報表（`realprice_alert.py`）| ✅ 完成 |
| v4.1 | 主清冊乾淨化（移除 externalLinks、欄位瘦身至 24 欄）| ✅ 完成 |
| v4.2 | 資料品質檢查（`check_land_master_quality.py`）+ 郵遞區號自動補正（`apply_postal_fix.py`）| ✅ 完成 |
| v5 | 電傳解析 → 自動更新謄本事件（`process_land_transcripts.py`）| ✅ 完成 |
| v6 | 月排程：定期實價比對 + 推播 | 規劃中 |

---

## 七、重要設計決策記錄

| 決策 | 說明 |
|------|------|
| event_key 設計 | SHA16(`land_match_key\|owner_key\|reg_seq\|reg_date\|reg_reason\|share_numer\|share_denom`)，保留同地主同地號不同時期的歷史登記 |
| 已售出不排除 | `is_sold=1` 代表地主已變現，仍可能是未來投資客戶，不從清冊移除 |
| 實價提醒不直接改主資料 | 實價命中 = 高度疑似已售出，需調閱新謄本確認後才由人工標記 |
| 主清冊與報表分離 | Python 不修改主清冊，實價提醒輸出獨立報表 |
| Telegram 回填 append 模式 | `note`、`phone` 只追加不覆蓋，保留歷史記錄 |

---

*文件位置：`docs/land_master_schema_v1.md`*
*主清冊：`土地主清冊_乾淨版_20260522.xlsx`（24 欄，無 externalLinks）*
