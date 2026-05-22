#!/usr/bin/env python3
"""
monthly_update_guard.py

Decides whether the scheduled LAND monthly update needs to run.
No schema changes: success markers are stored in update_history.action_required.
"""
import argparse
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
LOG_DIR = PROJECT_ROOT / 'logs'
SUCCESS_PREFIX = 'monthly_update_success:'
EXECUTION_TIME = time(8, 30)


def latest_due_window(ref: datetime) -> date:
    """Latest LAND system execution window: 2/12/22 08:30."""
    today = ref.date()
    for day in (22, 12, 2):
        candidate = date(today.year, today.month, day)
        if today > candidate or (today == candidate and ref.time() >= EXECUTION_TIME):
            return candidate
    previous_month = today.replace(day=1) - timedelta(days=1)
    return date(previous_month.year, previous_month.month, 22)


def marker_value(window: date) -> str:
    return f'{SUCCESS_PREFIX}{window.isoformat()}'


def has_success_marker(window: date) -> bool:
    if not DB_PATH.exists():
        return False
    with sqlite3.connect(str(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM update_history
            WHERE action_required = ?
            LIMIT 1
            """,
            (marker_value(window),),
        ).fetchone()
    return row is not None


def has_success_log(window: date) -> bool:
    if not LOG_DIR.exists():
        return False
    for path in sorted(LOG_DIR.glob('monthly_update_*.log'), reverse=True):
        suffix = path.stem.replace('monthly_update_', '')
        try:
            log_date = datetime.strptime(suffix, '%Y%m%d').date()
        except ValueError:
            continue
        if log_date < window:
            continue
        try:
            text = path.read_text(errors='replace')
        except OSError:
            continue
        if 'LAND 月更新 完成' in text:
            return True
    return False


def current_db_snapshot() -> tuple[str | None, str | None]:
    if not DB_PATH.exists():
        return None, None
    with sqlite3.connect(str(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT
              MIN(trade_date),
              MAX(trade_date)
            FROM land_transactions
            WHERE trade_date BETWEEN '1990-01-01' AND '2100-01-01'
            """
        ).fetchone()
    return row if row else (None, None)


def mark_success(window: date) -> None:
    if not DB_PATH.exists():
        raise SystemExit(f'DB not found: {DB_PATH}')
    coverage_start, coverage_end = current_db_snapshot()
    with sqlite3.connect(str(DB_PATH)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM update_history WHERE action_required = ? LIMIT 1",
            (marker_value(window),),
        ).fetchone()
        if exists:
            print(f'本期成功標記已存在：{window}')
            return
        conn.execute(
            """
            INSERT INTO update_history (
                last_trade_date,
                coverage_start,
                coverage_end,
                missing_intervals,
                action_required
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                coverage_end,
                coverage_start,
                coverage_end,
                '[]',
                marker_value(window),
            ),
        )
        conn.commit()
    print(f'已寫入本期月更新成功標記：{window}')


def status(ref: datetime) -> int:
    window = latest_due_window(ref)
    print(f'本期系統執行窗口：{window} 08:30')
    if has_success_marker(window):
        print('狀態：已完成（update_history success marker）')
        return 0
    if has_success_log(window):
        print('狀態：已完成（找到完成 log；尚未補 success marker）')
        return 0
    print('狀態：需要補跑 monthly_update.sh')
    return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', action='store_true', help='Check whether catch-up is required.')
    parser.add_argument('--mark-success', action='store_true', help='Mark current due window as successfully completed.')
    parser.add_argument('--date', help='Override current time for testing, YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS.')
    return parser.parse_args()


def parse_ref(value: str | None) -> datetime:
    if not value:
        return datetime.now()
    if 'T' in value:
        return datetime.fromisoformat(value)
    return datetime.combine(date.fromisoformat(value), time.min)


def main() -> int:
    args = parse_args()
    ref = parse_ref(args.date)
    window = latest_due_window(ref)
    if args.mark_success:
        mark_success(window)
        return 0
    return status(ref)


if __name__ == '__main__':
    sys.exit(main())
