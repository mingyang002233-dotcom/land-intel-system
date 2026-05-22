#!/usr/bin/env python3
"""
cleanup_orphan_land_details.py
清除 land_details 中找不到對應 land_transactions.record_id 的孤立資料。
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'


def cleanup_orphans(db_path=None, verbose=True):
    db_path = Path(db_path or DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f'DB not found: {db_path}')

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    before = cur.execute('SELECT COUNT(*) FROM land_details').fetchone()[0]
    orphan_count = cur.execute("""
        SELECT COUNT(*) FROM land_details
        WHERE record_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM land_transactions lt
            WHERE lt.record_id = land_details.record_id
        )
    """).fetchone()[0]
    null_count = cur.execute(
        'SELECT COUNT(*) FROM land_details WHERE record_id IS NULL'
    ).fetchone()[0]

    if verbose:
        print(f'land_details 清理前總筆數: {before}')
        print(f'  孤立 (record_id 無對應 transaction): {orphan_count}')
        print(f'  record_id IS NULL:                   {null_count}')

    cur.execute("""
        DELETE FROM land_details
        WHERE record_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM land_transactions lt
            WHERE lt.record_id = land_details.record_id
        )
    """)
    conn.commit()

    after = cur.execute('SELECT COUNT(*) FROM land_details').fetchone()[0]
    deleted = before - after

    if verbose:
        print(f'已刪除孤立筆數: {deleted}')
        print(f'land_details 清理後總筆數: {after}')

    conn.close()
    return deleted


if __name__ == '__main__':
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    cleanup_orphans(db_path)
