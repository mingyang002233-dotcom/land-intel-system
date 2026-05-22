#!/usr/bin/env python3

import sqlite3
from datetime import date, timedelta
from pathlib import Path
import json

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config' / 'realprice_config.json'
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
LOG_DIR = PROJECT_ROOT / 'logs'


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_iso_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def get_last_trade_date(conn):
    cursor = conn.execute('SELECT MAX(trade_date) FROM land_transactions')
    return parse_iso_date(cursor.fetchone()[0])


def get_last_import_time(conn):
    cursor = conn.execute('SELECT imported_at FROM import_logs ORDER BY imported_at DESC LIMIT 1')
    row = cursor.fetchone()
    if not row:
        return None
    try:
        return date.fromisoformat(row[0].split(' ')[0])
    except Exception:
        return None


def latest_official_release_day(now):
    """內政部官方資料發布日：每月 1/11/21。"""
    if now.day >= 21:
        return date(now.year, now.month, 21)
    if now.day >= 11:
        return date(now.year, now.month, 11)
    if now.day >= 1:
        return date(now.year, now.month, 1)
    previous_month = now.replace(day=1) - timedelta(days=1)
    return date(previous_month.year, previous_month.month, 21)


def latest_system_execution_day(now):
    """老蕭 LAND 系統執行日：每月 2/12/22 08:30。"""
    if now.day >= 22:
        return date(now.year, now.month, 22)
    if now.day >= 12:
        return date(now.year, now.month, 12)
    if now.day >= 2:
        return date(now.year, now.month, 2)
    previous_month = now.replace(day=1) - timedelta(days=1)
    return date(previous_month.year, previous_month.month, 22)


def check_missing_intervals(last_trade_date, window_days=180):
    if last_trade_date is None:
        return ['無法判斷，資料庫尚未建立或資料不足']
    threshold = date.today() - timedelta(days=window_days)
    if last_trade_date < threshold:
        return [f'最後交易日期 {last_trade_date}，未達近半年資料要求']
    return []


def print_status(last_trade_date, last_import_date, missing_intervals, config):
    print('=== 土地成交資料庫更新檢查 ===')
    print(f'資料庫路徑：{DB_PATH}')
    print(f'最後交易日期：{last_trade_date or "無"}')
    print(f'最後匯入日期：{last_import_date or "無"}')
    print(f'設定近半年資料要求：{config["time_window_months"]} 個月')
    print()
    if missing_intervals:
        print('需要補跑檢查：')
        for item in missing_intervals:
            print(f'  - {item}')
    else:
        print('目前資料狀態良好，近半年資料已涵蓋。')
    today = date.today()
    official_day = latest_official_release_day(today)
    system_day = latest_system_execution_day(today)
    print(f'官方資料發布日：{official_day} (內政部通常每月 1/11/21 釋出資料)')
    print(f'系統執行窗口：{system_day} 08:30 (老蕭 LAND 每月 2/12/22 執行)')
    if last_import_date is None or last_import_date < system_day:
        print('建議補跑：最近更新時間可能落後老蕭 LAND 系統執行窗口。')


def main():
    config = load_config()
    config['time_window_months'] = config.get('time_window_months', 6)
    if not DB_PATH.exists():
        print(f'資料庫不存在：{DB_PATH}')
        print('請先執行 scripts/init_db.py 以及 scripts/parse_realprice.py 進行資料匯入。')
        return
    with sqlite3.connect(DB_PATH) as conn:
        last_trade_date = get_last_trade_date(conn)
        last_import_date = get_last_import_time(conn)
    missing_intervals = check_missing_intervals(last_trade_date, config['time_window_months'] * 30)
    print_status(last_trade_date, last_import_date, missing_intervals, config)


if __name__ == '__main__':
    main()
