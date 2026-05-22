#!/usr/bin/env python3
"""
check_update_schedule.py
檢查 DB 現有資料覆蓋狀況，自動判斷民國年份，列出待下載的增量期別。

不寫死年份：以今日日期自動推算當前民國年（西元 - 1911）。
"""
import ssl
import sqlite3
import re
import urllib.request
import json
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
HISTORY_ZIP_ROOT = PROJECT_ROOT / 'csv' / 'history'
MONTHLY_ZIP_ROOT = PROJECT_ROOT / 'csv' / 'monthly'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def western_to_roc(western_year: int) -> int:
    return western_year - 1911


def roc_to_western(roc_year: int) -> int:
    return roc_year + 1911


def today_roc() -> int:
    return western_to_roc(date.today().year)


def get_update_windows(ref: date) -> list[date]:
    """最近三個月的老蕭 LAND 系統執行窗口（2/12/22），過去的，倒序。"""
    windows = []
    for month_offset in range(3):
        y, m = ref.year, ref.month - month_offset
        if m <= 0:
            m += 12
            y -= 1
        for day in (22, 12, 2):
            try:
                d = date(y, m, day)
                if d <= ref:
                    windows.append(d)
            except ValueError:
                pass
    return sorted(windows, reverse=True)


def next_update_window(ref: date) -> date:
    """下一個老蕭 LAND 系統執行窗口（2/12/22 08:30）。"""
    for day in (2, 12, 22):
        try:
            c = date(ref.year, ref.month, day)
            if c > ref:
                return c
        except ValueError:
            pass
    m = ref.month + 1 if ref.month < 12 else 1
    y = ref.year if ref.month < 12 else ref.year + 1
    return date(y, m, 2)


def fetch_available_monthly_periods() -> list[str]:
    """從官方 DownloadHistory_ajax_list 取得本期可下載的期別（YYYYMMDD 格式）。"""
    url = 'https://plvr.land.moi.gov.tw/DownloadHistory_ajax_list'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'land-intel/1.0'})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            html = r.read().decode('utf-8')
        return sorted(re.findall(r"downloadLast\('(\d{8})'\)", html))
    except Exception as e:
        print(f'  [警告] 無法取得本期清單：{e}')
        return []


def monthly_zip_path(period: str) -> Path:
    """YYYYMMDD → csv/monthly/YYYY/YYYYMMDD_all.zip"""
    return MONTHLY_ZIP_ROOT / period[:4] / f'{period}_all.zip'


def season_zip_path(season: str) -> Path:
    """114S1 → csv/history/114/114S1_all.zip"""
    roc_year = season[:3]
    return HISTORY_ZIP_ROOT / roc_year / f'{season}_all.zip'


def get_db_year_counts(conn) -> dict[str, int]:
    rows = conn.execute("""
        SELECT STRFTIME('%Y', trade_date) yr, COUNT(*) c
        FROM land_transactions
        WHERE trade_date BETWEEN '1990-01-01' AND '2100-01-01'
        GROUP BY yr
    """).fetchall()
    return {r[0]: r[1] for r in rows}


def record_update_history(conn, max_date: str | None, available: list[str], to_download: list[str]) -> None:
    """Record the post-check coverage snapshot for monthly_update.sh audits."""
    coverage_start = conn.execute(
        "SELECT MIN(trade_date) FROM land_transactions "
        "WHERE trade_date BETWEEN '1990-01-01' AND '2100-01-01'"
    ).fetchone()[0]
    if to_download:
        action_required = 'download_required:' + ','.join(to_download)
    elif available:
        action_required = 'none'
    else:
        action_required = 'official_list_unavailable'

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
            max_date,
            coverage_start,
            max_date,
            json.dumps(to_download, ensure_ascii=False),
            action_required,
        ),
    )
    conn.commit()


def main():
    today = date.today()
    roc_now = today_roc()

    if not DB_PATH.exists():
        print(f'DB not found: {DB_PATH}')
        return

    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute('SELECT COUNT(*) FROM land_transactions').fetchone()[0]
    max_date = conn.execute(
        "SELECT MAX(trade_date) FROM land_transactions "
        "WHERE trade_date BETWEEN '1990-01-01' AND '2100-01-01'"
    ).fetchone()[0]
    year_counts = get_db_year_counts(conn)

    sep = '─' * 56
    print(sep)
    print('  LAND 更新狀態檢查')
    print(sep)
    print(f'今日（民國）:     {roc_now}年{today.month}月{today.day}日  ({today})')
    print(f'DB 總筆數:        {total:,}')
    print(f'最晚成交日期:     {max_date}')
    print()

    # ── 近三年資料量（動態計算）──────────────────────────
    print('近三年各年資料量：')
    needs_history = []
    for offset in range(3):
        w_year = today.year - offset
        r_year = western_to_roc(w_year)
        cnt = year_counts.get(str(w_year), 0)
        status = f'{cnt:,} 筆'
        if cnt == 0:
            status += '  ⚠️ 無資料'
            needs_history.append(str(r_year))
        elif cnt < 5000 and offset > 0:
            status += '  ⚠️ 筆數偏少，建議確認是否完整'
        print(f'  {r_year}年（{w_year}）: {status}')
    print()

    # ── 本期增量：官方可下載期別 ────────────────────────
    print('本期增量（官方 DownloadHistory）：')
    available = fetch_available_monthly_periods()
    to_download = []
    for period in available:
        path = monthly_zip_path(period)
        d = date(int(period[:4]), int(period[4:6]), int(period[6:8]))
        roc_y = western_to_roc(d.year)
        label = f'{roc_y}年{d.month}月{d.day}日'
        status = '✅ 已下載' if path.exists() else '⬜ 未下載'
        if not path.exists():
            to_download.append(period)
        print(f'  {period}  {label}  {status}')
    print()

    record_update_history(conn, max_date, available, to_download)
    conn.close()
    print('更新紀錄：已寫入 update_history')
    print()

    # ── 更新窗口 ────────────────────────────────────────
    past = get_update_windows(today)
    last_window = past[0] if past else today
    nxt = next_update_window(today)
    days_since = (today - last_window).days
    print(f'最近系統執行窗口: {last_window} 08:30（{days_since} 天前）')
    print(f'下次系統執行窗口: {nxt} 08:30（{(nxt - today).days} 天後）')
    print('官方資料發布日:   每月 1 / 11 / 21（內政部可能釋出新資料）')
    print()

    # ── 建議 ─────────────────────────────────────────────
    print('建議：')
    if needs_history:
        for ry in needs_history:
            print(f'  ⬜ 補下載歷史資料：{ry}年  '
                  f'python3 scripts/download_realprice.py --year {ry}')
    if to_download:
        for p in to_download:
            print(f'  ⬜ 下載增量期別：{p}  '
                  f'python3 scripts/download_realprice.py --period {p}')
    elif available:
        latest_official = available[-1]
        d = date(int(latest_official[:4]), int(latest_official[4:6]), int(latest_official[6:8]))
        roc = d.year - 1911
        print(f'  ✅ 已匯入官方最新期別：{latest_official}（{roc}年{d.month}月{d.day}日）')
        print(f'  ⏳ 下一個系統執行窗口：{nxt} 08:30（{roc_now}年{nxt.month}月{nxt.day}日）')
        print(f'     屆時執行：python3 scripts/download_realprice.py --period auto && python3 scripts/maintenance_pipeline.py')
    if not needs_history and not to_download and not available:
        print('  ⚠️  無法取得官方期別清單，請確認網路連線')
    if days_since >= 10 and not to_download:
        print(f'  ⚠️  距上次窗口 {days_since} 天，建議手動確認官方是否有新期別')
    print(sep)


if __name__ == '__main__':
    main()
