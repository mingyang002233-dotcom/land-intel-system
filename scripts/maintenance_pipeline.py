#!/usr/bin/env python3
"""
maintenance_pipeline.py
一鍵執行資料維護流程。

自動掃描順序（每個目錄獨立 parse，已匯入資料不重複增加）：
  1. csv/lvr_landcsv/          — 官方最新本期（季度）
  2. csv/history/<年>/*/       — 補下載的歷史季度（解壓目錄）
  3. csv/monthly/<年>/<期>/    — 增量期別（解壓目錄）

之後執行：
  4. backfill_section_name.py
  5. cleanup_orphan_land_details.py
  6. validate_land_data.py

用法：
  python3 scripts/maintenance_pipeline.py               # 自動掃描所有目錄
  python3 scripts/maintenance_pipeline.py --csv-dir csv/monthly/2026/20260511
"""
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'


def get_row_count() -> int:
    conn = sqlite3.connect(str(DB_PATH))
    n = conn.execute('SELECT COUNT(*) FROM land_transactions').fetchone()[0]
    conn.close()
    return n


def collect_csv_dirs(extra_dirs: list[Path] | None = None) -> list[Path]:
    """
    收集所有含 CSV 的目錄，順序：
    1. 額外指定目錄（--csv-dir）
    2. csv/lvr_landcsv/
    3. csv/history/<年>/<季>/  (解壓目錄，含 *.csv)
    4. csv/monthly/<年>/<期>/  (解壓目錄，含 *.csv)
    """
    dirs: list[Path] = []

    if extra_dirs:
        dirs.extend(extra_dirs)

    # 官方最新本期
    main_dir = PROJECT_ROOT / 'csv' / 'lvr_landcsv'
    if main_dir.exists() and list(main_dir.glob('*.csv')):
        if main_dir not in dirs:
            dirs.append(main_dir)

    # 歷史季度解壓目錄：csv/history/<年>/<季>/
    history_root = PROJECT_ROOT / 'csv' / 'history'
    if history_root.exists():
        for year_dir in sorted(history_root.iterdir()):
            if not year_dir.is_dir():
                continue
            for season_dir in sorted(year_dir.iterdir()):
                if season_dir.is_dir() and list(season_dir.glob('*.csv')):
                    if season_dir not in dirs:
                        dirs.append(season_dir)

    # 增量解壓目錄：csv/monthly/<年>/<期>/
    monthly_root = PROJECT_ROOT / 'csv' / 'monthly'
    if monthly_root.exists():
        for year_dir in sorted(monthly_root.iterdir()):
            if not year_dir.is_dir():
                continue
            for period_dir in sorted(year_dir.iterdir()):
                if period_dir.is_dir() and list(period_dir.glob('*.csv')):
                    if period_dir not in dirs:
                        dirs.append(period_dir)

    return dirs


def run_parse(csv_dir: Path) -> tuple[bool, int]:
    """執行 parse，回傳 (成功, 新增筆數)。"""
    cmd = [sys.executable, str(SCRIPTS / 'parse_realprice.py'), str(csv_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    new_rows = 0
    for line in result.stdout.splitlines():
        if '實際新增' in line:
            try:
                new_rows = int(line.split(':')[1].strip())
            except Exception:
                pass
    return result.returncode == 0, new_rows


def run_step(label: str, cmd: list[str]) -> bool:
    print(f'\n{"─"*52}')
    print(f'[{label}]')
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=False, text=True)
    elapsed = time.perf_counter() - t0
    if result.returncode == 0:
        print(f'✅ 完成  ({elapsed:.1f}s)')
        return True
    print(f'❌ 失敗  (exit {result.returncode}, {elapsed:.1f}s)')
    if result.stderr:
        print('  STDERR:', result.stderr.strip()[:400])
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='LAND 資料維護 pipeline')
    parser.add_argument('--csv-dir', action='append', metavar='PATH',
                        help='額外指定 CSV 目錄（可重複）')
    args = parser.parse_args()

    extra = [Path(d) for d in args.csv_dir] if args.csv_dir else None

    print('=' * 52)
    print('  LAND 資料維護流程')
    print('=' * 52)
    total_start = time.perf_counter()

    before = get_row_count()
    print(f'匯入前 DB 總筆數: {before:,}')

    # ── Step 1：Parse 所有 CSV 目錄 ──────────────────────
    csv_dirs = collect_csv_dirs(extra)
    print(f'\n掃描 CSV 目錄共 {len(csv_dirs)} 個：')
    for d in csv_dirs:
        rel = d.relative_to(PROJECT_ROOT)
        print(f'  {rel}')

    print()
    total_new = 0
    parse_failed = []
    for d in csv_dirs:
        rel = str(d.relative_to(PROJECT_ROOT))
        ok, new_rows = run_parse(d)
        status = '✅' if ok else '❌'
        print(f'  {status} {rel:<55} 新增 {new_rows:,} 筆')
        total_new += new_rows
        if not ok:
            parse_failed.append(rel)

    after_parse = get_row_count()
    print(f'\nParse 完成：實際新增 {after_parse - before:,} 筆（DB: {after_parse:,}）')

    if parse_failed:
        print(f'❌ Parse 失敗目錄（{len(parse_failed)} 個）：')
        for f in parse_failed:
            print(f'  {f}')
        print('流程中止。請修正後重新執行。')
        sys.exit(1)

    # ── Step 2~4：後處理 ─────────────────────────────────
    post_steps = [
        ('回填 section_name',   [sys.executable, str(SCRIPTS / 'backfill_section_name.py')]),
        ('清孤立 land_details', [sys.executable, str(SCRIPTS / 'cleanup_orphan_land_details.py')]),
        ('資料品質驗證',         [sys.executable, str(SCRIPTS / 'validate_land_data.py')]),
    ]
    for label, cmd in post_steps:
        ok = run_step(label, cmd)
        if not ok:
            print(f'\n❌ 流程中止於：{label}')
            sys.exit(1)

    after = get_row_count()
    elapsed = time.perf_counter() - total_start
    print(f'\n{"=" * 52}')
    print(f'✅ 維護流程完成  ({elapsed:.1f}s)')
    print(f'   匯入前: {before:,}  →  匯入後: {after:,}  (新增 {after - before:,} 筆)')
    print('=' * 52)


if __name__ == '__main__':
    main()
