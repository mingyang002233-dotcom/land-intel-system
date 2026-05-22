#!/usr/bin/env python3

from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
SCHEMA_PATH = PROJECT_ROOT / 'config' / 'sqlite_schema.sql'


def init_db(db_path: Path = None):
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"SQLite schema not found: {SCHEMA_PATH}")

    with sqlite3.connect(db_path) as conn:
        sql = SCHEMA_PATH.read_text(encoding='utf-8')
        conn.executescript(sql)

    print(f"Initialized SQLite database at: {db_path}")


if __name__ == '__main__':
    init_db()
