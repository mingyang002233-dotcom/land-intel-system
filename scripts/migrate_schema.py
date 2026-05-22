#!/usr/bin/env python3
"""
Migrate land_intel.db from v1 → v2 schema.

策略（不破壞原始資料）：
  1. 對既存表 land_transactions 做 ALTER TABLE ADD COLUMN（SQLite 支援，
     僅能加在最後且 NULLABLE，舊資料新欄位都會是 NULL；符合 v4「禁止刪除原始資料」）。
  2. 建立新副表 land_details / build_details / park_details（若不存在）。
  3. 更新 import_logs / update_history 的新欄位。
  4. 重建索引。

執行：
  python3 scripts/migrate_schema.py
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
SCHEMA_PATH = PROJECT_ROOT / 'config' / 'sqlite_schema.sql'


# 想要在 land_transactions 上 ALTER 加上的新欄位
ALTERS_LAND_TX = [
    ("source_kind",       "TEXT"),
    ("source_file_type",  "TEXT"),
    ("target_category",   "TEXT"),
    ("building_area_sqm", "REAL"),
    ("building_type",     "TEXT"),
    ("main_use",          "TEXT"),
    ("main_material",     "TEXT"),
    ("record_id",         "TEXT"),
    ("raw_json",          "TEXT"),
    ("original_csv_row",  "TEXT"),
    ("parse_status",      "TEXT DEFAULT 'ok'"),
    ("parse_warnings",    "TEXT"),
]

# 想要在 import_logs 上 ALTER 加上的新欄位
ALTERS_IMPORT_LOGS = [
    ("source_kind",   "TEXT"),
    ("rows_total",    "INTEGER"),
    ("parse_partial", "INTEGER"),
    ("parse_failed",  "INTEGER"),
]


def existing_columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def existing_tables(conn):
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def alter_add_if_missing(conn, table, additions):
    cols = existing_columns(conn, table)
    for name, typ in additions:
        if name not in cols:
            sql = f"ALTER TABLE {table} ADD COLUMN {name} {typ}"
            print(f"  + {table}.{name}  ({typ})")
            conn.execute(sql)


def run_full_schema(conn):
    """執行 sqlite_schema.sql 的 CREATE IF NOT EXISTS，建立新表+索引。"""
    sql = SCHEMA_PATH.read_text(encoding='utf-8')
    conn.executescript(sql)


def main():
    if not DB_PATH.exists():
        # 沒有 DB → 直接建空 DB 並套整套 schema
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(f"DB not found, creating fresh: {DB_PATH}")
        with sqlite3.connect(DB_PATH) as conn:
            run_full_schema(conn)
        print("Done. Fresh DB created with v2 schema.")
        return

    print(f"Migrating {DB_PATH} → v2 schema (preserving all existing rows)")
    with sqlite3.connect(DB_PATH) as conn:
        tables = existing_tables(conn)

        # 1) ALTER 既有表
        if 'land_transactions' in tables:
            print("ALTER land_transactions:")
            alter_add_if_missing(conn, 'land_transactions', ALTERS_LAND_TX)
        else:
            print("land_transactions not found, will be created.")

        if 'import_logs' in tables:
            print("ALTER import_logs:")
            alter_add_if_missing(conn, 'import_logs', ALTERS_IMPORT_LOGS)
        else:
            print("import_logs not found, will be created.")

        # 2) 建立缺少的表 + 索引（IF NOT EXISTS，不會覆蓋）
        print("Running CREATE IF NOT EXISTS for all v2 tables/indexes…")
        run_full_schema(conn)

        # 3) 對歷史資料補上 source_kind/source_file_type/target_category
        #    舊版只匯入 _a 主檔且只接受 transaction_target='土地'
        #    這裡安全地補一個保底值，不會誤標其它
        print("Backfilling source_kind/source_file_type for legacy rows…")
        conn.execute("""
            UPDATE land_transactions
               SET source_kind = COALESCE(source_kind, 'sale'),
                   source_file_type = COALESCE(source_file_type, 'main')
             WHERE source_kind IS NULL OR source_file_type IS NULL
        """)

        # 對舊資料粗略歸類（土地 → '土地'；這只是初值，重新跑 parser 會覆寫）
        conn.execute("""
            UPDATE land_transactions
               SET target_category = '土地'
             WHERE target_category IS NULL
               AND transaction_target = '土地'
        """)

        conn.commit()

    print("Migration complete.")


if __name__ == '__main__':
    main()
