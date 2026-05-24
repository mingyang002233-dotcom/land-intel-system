#!/usr/bin/env python3
"""
check_trade_date_range.py
唯讀檢查 land_transactions.trade_date 是否超出允許範圍。

檢查範圍：
  1990-01-01 <= trade_date <= 2100-01-01

輸出：
  - 異常筆數
  - 最小異常日期
  - 最大異常日期
  - 前 20 筆異常資料

此腳本只以 SQLite 唯讀模式連線，不修改任何資料。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "land_intel.db"
MIN_DATE = "1990-01-01"
MAX_DATE = "2100-01-01"


def open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def fetch_summary(conn: sqlite3.Connection) -> tuple[int, str | None, str | None]:
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS abnormal_count,
            MIN(trade_date) AS min_abnormal_date,
            MAX(trade_date) AS max_abnormal_date
        FROM land_transactions
        WHERE trade_date < ? OR trade_date > ?
        """,
        (MIN_DATE, MAX_DATE),
    ).fetchone()
    return int(row[0]), row[1], row[2]


def fetch_samples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT
            id,
            record_id,
            city,
            district,
            section_name,
            land_number,
            trade_date,
            source_file
        FROM land_transactions
        WHERE trade_date < ? OR trade_date > ?
        ORDER BY trade_date ASC, id ASC
        LIMIT 20
        """,
        (MIN_DATE, MAX_DATE),
    ).fetchall()


def print_report(abnormal_count: int, min_date: str | None, max_date: str | None,
                 rows: list[sqlite3.Row]) -> None:
    sep = "─" * 72
    print(sep)
    print("  land_transactions.trade_date 範圍檢查（唯讀）")
    print(sep)
    print(f"允許範圍:     {MIN_DATE} ~ {MAX_DATE}")
    print(f"異常筆數:     {abnormal_count:,}")
    print(f"最小異常日期: {min_date or '—'}")
    print(f"最大異常日期: {max_date or '—'}")
    print()

    if not rows:
        print("前 20 筆異常資料: 無")
        print(sep)
        return

    print("前 20 筆異常資料:")
    for idx, row in enumerate(rows, 1):
        section = row["section_name"] or ""
        land_no = row["land_number"] or ""
        location = " ".join(part for part in [row["city"], row["district"], section, land_no] if part)
        print(
            f"{idx:02d}. id={row['id']} record_id={row['record_id'] or '—'} "
            f"trade_date={row['trade_date']} location={location or '—'} "
            f"source_file={row['source_file'] or '—'}"
        )
    print(sep)


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1

    with open_readonly(db_path) as conn:
        abnormal_count, min_date, max_date = fetch_summary(conn)
        rows = fetch_samples(conn)

    print_report(abnormal_count, min_date, max_date, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
