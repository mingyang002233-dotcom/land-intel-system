#!/usr/bin/env python3
"""
download_realprice.py
從內政部實價登錄官方網站下載 CSV。

兩種下載模式：

1. 季度歷史下載（DownloadSeason）
   URL: /DownloadSeason?season=<年>S<季>&type=zip&fileName=lvr_landcsv.zip
   季別代碼：S1~S4（S1=前年12/11~當年3/10 ... S4=9/11~12/10）
   存放：csv/history/<民國年>/<民國年>S<季>_all.zip

2. 本期增量下載（DownloadHistory）
   URL: /DownloadHistory?fileName=<YYYYMMDD>
   存放：csv/monthly/<西元年>/<YYYYMMDD>_all.zip

不寫死年份：以今日日期自動推算民國年。

用法：
  python3 scripts/download_realprice.py --list
  python3 scripts/download_realprice.py --season 114S1
  python3 scripts/download_realprice.py --year 114          # 整年4季
  python3 scripts/download_realprice.py --year auto         # 當前民國年整年
  python3 scripts/download_realprice.py --period 20260501   # 單期增量
  python3 scripts/download_realprice.py --period auto       # 所有未下載增量
  python3 scripts/download_realprice.py --dry-run --year auto
"""

import argparse
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = 'https://plvr.land.moi.gov.tw'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ── 民國年工具 ──────────────────────────────────────────────
def today_roc() -> int:
    return date.today().year - 1911


def roc_to_western(roc: int) -> int:
    return roc + 1911


# ── 路徑規則 ────────────────────────────────────────────────
def season_zip_path(season: str) -> Path:
    """114S1 → csv/history/114/114S1_all.zip"""
    roc_year = season[:3]
    return PROJECT_ROOT / 'csv' / 'history' / roc_year / f'{season}_all.zip'


def monthly_zip_path(period: str) -> Path:
    """20260501 → csv/monthly/2026/20260501_all.zip"""
    return PROJECT_ROOT / 'csv' / 'monthly' / period[:4] / f'{period}_all.zip'


# ── 官方可用季別清單（從 DownloadSeason_ajax_list 取得）──────
_SEASON_CACHE: list[str] | None = None


def fetch_available_seasons() -> list[str]:
    global _SEASON_CACHE
    if _SEASON_CACHE is not None:
        return _SEASON_CACHE
    url = f'{BASE_URL}/DownloadSeason_ajax_list'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'land-intel/1.0'})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            html = r.read().decode('utf-8')
        seasons = re.findall(r'value="(\d{3}S[1-4])"', html)
        _SEASON_CACHE = seasons
        return seasons
    except Exception as e:
        print(f'[警告] 無法取得季別清單：{e}')
        # 預設 fallback（101~當前年）
        roc = today_roc()
        fallback = []
        for y in range(101, roc + 1):
            for q in range(4, 0, -1):
                fallback.append(f'{y}S{q}')
        _SEASON_CACHE = fallback
        return fallback


def seasons_for_year(roc_year: int) -> list[str]:
    prefix = str(roc_year)
    return [s for s in fetch_available_seasons() if s.startswith(prefix)]


# ── 官方可用本期增量清單 ─────────────────────────────────────
def fetch_available_periods() -> list[str]:
    url = f'{BASE_URL}/DownloadHistory_ajax_list'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'land-intel/1.0'})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            html = r.read().decode('utf-8')
        return sorted(re.findall(r"downloadLast\('(\d{8})'\)", html))
    except Exception as e:
        print(f'[警告] 無法取得本期清單：{e}')
        return []


# ── 下載核心 ─────────────────────────────────────────────────
def _download(url: str, out_path: Path, label: str, dry_run: bool) -> bool:
    if out_path.exists():
        print(f'  [skip] {label} 已存在：{out_path.name}')
        return True
    if dry_run:
        print(f'  [dry]  {label}')
        print(f'         URL: {url}')
        print(f'         → {out_path}')
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f'  下載 {label} ...', end=' ', flush=True)
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; land-intel/1.0)',
            'Referer': f'{BASE_URL}/DownloadOpenData',
        }
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=180) as resp:
            data = resp.read()
        if data[:2] != b'PK':
            print(f'FAIL (收到非 ZIP 回應，{len(data)} bytes)')
            return False
        out_path.write_bytes(data)
        elapsed = time.perf_counter() - t0
        print(f'OK ({len(data)//1024:,} KB, {elapsed:.1f}s)')
        return True
    except urllib.error.URLError as e:
        print(f'FAIL ({e})')
        return False


def download_season(season: str, dry_run: bool = False) -> bool:
    season = season.upper()
    url = f'{BASE_URL}/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip'
    return _download(url, season_zip_path(season), season, dry_run)


def download_period(period: str, dry_run: bool = False) -> bool:
    url = f'{BASE_URL}/DownloadHistory?fileName={period}'
    return _download(url, monthly_zip_path(period), period, dry_run)


# ── 列表 ─────────────────────────────────────────────────────
def list_all(roc_year: int | None = None):
    seasons = fetch_available_seasons()
    if roc_year:
        seasons = [s for s in seasons if s.startswith(str(roc_year))]

    print('=== 季度歷史 ===')
    for s in seasons:
        status = '✅' if season_zip_path(s).exists() else '⬜'
        print(f'  {status} {s}')

    print()
    print('=== 本期增量 ===')
    for p in fetch_available_periods():
        status = '✅' if monthly_zip_path(p).exists() else '⬜'
        d = date(int(p[:4]), int(p[4:6]), int(p[6:8]))
        roc = d.year - 1911
        print(f'  {status} {p}  （{roc}年{d.month}月{d.day}日）')


# ── main ──────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='內政部實價登錄 CSV 下載器（季度 + 增量）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--list', action='store_true', help='列出可下載項目')
    parser.add_argument('--season', action='append', metavar='114S1',
                        help='下載季度（可重複）')
    parser.add_argument('--year', action='append', metavar='114|auto',
                        help='下載整年4季，auto=當前民國年（可重複）')
    parser.add_argument('--period', action='append', metavar='20260501|auto',
                        help='下載本期增量，auto=所有未下載（可重複）')
    parser.add_argument('--dry-run', action='store_true', help='只顯示，不下載')
    args = parser.parse_args()

    if args.list:
        list_all()
        return

    season_targets: list[str] = []
    period_targets: list[str] = []

    # 季度
    if args.year:
        for y in args.year:
            roc = today_roc() if y.lower() == 'auto' else int(y)
            seasons = seasons_for_year(roc)
            if not seasons:
                print(f'[警告] 找不到 {roc}年 的季別')
            for s in seasons:
                if s not in season_targets:
                    season_targets.append(s)

    if args.season:
        for s in args.season:
            s = s.upper()
            if s not in season_targets:
                season_targets.append(s)

    # 增量
    if args.period:
        for p in args.period:
            if p.lower() == 'auto':
                available = fetch_available_periods()
                for ap in available:
                    if not monthly_zip_path(ap).exists() and ap not in period_targets:
                        period_targets.append(ap)
            else:
                if p not in period_targets:
                    period_targets.append(p)

    has_explicit_target = bool(args.year or args.season or args.period)
    if not has_explicit_target:
        parser.print_help()
        return
    if not season_targets and not period_targets:
        print('✅ 所有指定項目已是最新，無需下載。')
        return

    ok = fail = 0

    if season_targets:
        print(f'季度下載：{len(season_targets)} 個')
        for s in season_targets:
            if download_season(s, args.dry_run):
                ok += 1
            else:
                fail += 1
        print()

    if period_targets:
        print(f'增量下載：{len(period_targets)} 個')
        for p in period_targets:
            if download_period(p, args.dry_run):
                ok += 1
            else:
                fail += 1
        print()

    print(f'完成：成功 {ok}，失敗 {fail}')
    if fail:
        sys.exit(1)


if __name__ == '__main__':
    main()
