-- =============================================================
-- 老蕭 LAND｜SQLite Schema v2
-- =============================================================
-- 設計原則（依 ENGINEERING_CORE.md / PROJECT_RULES.md / v4 PDF）：
--   1. 原始資料禁止刪除：所有 CSV 列以 raw_json 與 original_csv_row 完整保留
--   2. Query 階段才篩選：解析階段不過濾任何交易標的、用途、地區
--   3. 房地、土地、車位、工業、特殊交易全部入庫
--   4. 規則式分類欄位（target_category / source_kind / source_file_type）
--      只是「再加一層 query 友善欄位」，不取代任何原始欄位
--   5. 副檔（_land / _build / _park）獨立成表，用 record_id 對應主檔
--   6. 全部欄位 NULLABLE，避免任何單一 row 因解析失敗而被拒絕入庫
-- =============================================================

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- -------------------------------------------------------------
-- 主表：所有「主檔」(_a / _b / _c) 都進這張，用 source_kind 區分
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS land_transactions (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,

  -- ── 規則式分類（查詢用，不破壞原始資料）─────────────────
  source_kind              TEXT,        -- 'sale'(_a 不動產買賣) / 'presale'(_b 預售屋) / 'rent'(_c 租賃)
  source_file_type         TEXT,        -- 'main' / 'land_detail' / 'build_detail' / 'park_detail'
  target_category          TEXT,        -- '房屋' / '建地' / '農地' / '車位' / '工業' / '預售' / '租賃' / '特殊' / '其他'
  city                     TEXT,        -- 由檔名代碼判斷（a→台北市…），fallback 由 location_raw 抓
  district                 TEXT,        -- 鄉鎮市區
  section_name             TEXT,        -- 地段
  land_number              TEXT,        -- 地號
  location_raw             TEXT,        -- 土地位置建物門牌
  trade_date               TEXT,        -- ISO YYYY-MM-DD
  area_sqm                 REAL,        -- 土地移轉總面積平方公尺
  area_ping                REAL,        -- 換算坪
  building_area_sqm        REAL,        -- 建物移轉總面積平方公尺
  total_price              INTEGER,     -- 總價元
  total_price_wan          REAL,        -- 萬
  unit_price_per_sqm       REAL,        -- 單價元平方公尺
  unit_price_per_ping_wan  REAL,
  land_use_zone            TEXT,        -- 都市土地使用分區
  land_use_type            TEXT,        -- 非都市土地使用分區/編定
  building_type            TEXT,        -- 建物型態
  main_use                 TEXT,        -- 主要用途
  main_material            TEXT,        -- 主要建材
  transaction_type         TEXT,        -- 移轉層次 / 移轉情形
  transaction_target       TEXT,        -- 交易標的（房地 / 房地(車位) / 土地 / 建物 / 車位 …）
  note                     TEXT,        -- 備註
  record_id                TEXT,        -- 編號（給副檔用來對應）

  -- ── 原始資料保留（v4 鐵則：禁止刪除原始資料）─────────
  raw_json                 TEXT,        -- 整行 CSV 欄位 → JSON
  original_csv_row         TEXT,        -- 原始 CSV 行（rawtext，逗號分隔）

  -- ── 系統欄位 ────────────────────────────────────────
  source_file              TEXT,
  parse_status             TEXT DEFAULT 'ok',   -- 'ok' / 'partial' / 'unparseable'
  parse_warnings           TEXT,
  unique_key               TEXT UNIQUE,
  created_at               TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lt_trade_date       ON land_transactions(trade_date);
CREATE INDEX IF NOT EXISTS idx_lt_city_district    ON land_transactions(city, district);
CREATE INDEX IF NOT EXISTS idx_lt_target_category  ON land_transactions(target_category);
CREATE INDEX IF NOT EXISTS idx_lt_source_kind      ON land_transactions(source_kind);
CREATE INDEX IF NOT EXISTS idx_lt_record_id        ON land_transactions(record_id);
CREATE INDEX IF NOT EXISTS idx_lt_location         ON land_transactions(location_raw);

-- -------------------------------------------------------------
-- 副表：土地明細（_land.csv）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS land_details (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  source_kind       TEXT,           -- sale / presale / rent
  record_id         TEXT,           -- 對應 land_transactions.record_id
  city             TEXT,
  location_raw     TEXT,            -- 土地位置
  area_sqm         REAL,            -- 土地移轉面積平方公尺
  zoning           TEXT,            -- 使用分區或編定
  share_num        REAL,            -- 權利人持分分子
  share_den        REAL,            -- 權利人持分分母
  transfer_status  TEXT,            -- 移轉情形
  land_number      TEXT,            -- 地號
  raw_json         TEXT,
  original_csv_row TEXT,
  source_file      TEXT,
  unique_key       TEXT UNIQUE,
  created_at       TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ld_record_id ON land_details(record_id);

-- -------------------------------------------------------------
-- 副表：建物明細（_build.csv）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS build_details (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  source_kind       TEXT,
  record_id         TEXT,
  building_age      TEXT,           -- 屋齡
  building_area_sqm REAL,           -- 建物移轉面積平方公尺
  main_use          TEXT,           -- 主要用途
  main_material     TEXT,           -- 主要建材
  build_completion  TEXT,           -- 建築完成日期
  total_floors      TEXT,           -- 總層數
  building_floor    TEXT,           -- 建物分層
  transfer_status   TEXT,           -- 移轉情形
  raw_json          TEXT,
  original_csv_row  TEXT,
  source_file       TEXT,
  unique_key        TEXT UNIQUE,
  created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_bd_record_id ON build_details(record_id);

-- -------------------------------------------------------------
-- 副表：停車位明細（_park.csv）
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS park_details (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  source_kind       TEXT,
  record_id         TEXT,
  park_type         TEXT,           -- 車位類別
  park_price        REAL,           -- 車位價格
  park_area_sqm     REAL,           -- 車位面積平方公尺
  park_floor        TEXT,           -- 車位所在樓層
  raw_json          TEXT,
  original_csv_row  TEXT,
  source_file       TEXT,
  unique_key        TEXT UNIQUE,
  created_at        TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pd_record_id ON park_details(record_id);

-- -------------------------------------------------------------
-- 匯入紀錄
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_logs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file     TEXT,
  source_kind     TEXT,
  imported_at     TEXT DEFAULT (datetime('now','localtime')),
  rows_total      INTEGER,
  records_added   INTEGER,
  records_skipped INTEGER,
  parse_partial   INTEGER,
  parse_failed    INTEGER,
  status          TEXT,
  message         TEXT
);

-- -------------------------------------------------------------
-- 更新檢查
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS update_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  checked_at        TEXT DEFAULT (datetime('now','localtime')),
  last_trade_date   TEXT,
  coverage_start    TEXT,
  coverage_end      TEXT,
  missing_intervals TEXT,
  action_required   TEXT
);

COMMIT;
