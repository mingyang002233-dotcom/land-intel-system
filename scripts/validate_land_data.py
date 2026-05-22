#!/usr/bin/env python3
"""
validate_land_data.py
每次 CSV 匯入後執行，檢查資料品質。
輸出：parse 成功率、缺失率、疑似污染、重複、查詢覆蓋率。
"""
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'

ROAD_PAT = re.compile(r'(?:路|街|大道|公路|快速道路|高速公路)(?:[一二三四五六七八九十百千]|\d+)?段')
ADDR_KEYS = ('路', '街', '巷', '弄', '號')
PUA_PAT = re.compile(r'[-]')
COUNTY_PREFIX_PAT = re.compile(
    r'^(?:[^\s]{2,5}[市縣](?:[^\s]{2,5}[區鄉鎮])?(?:[^\s]{2,5}里)?)(.{2,}(?:小段|段))$'
)


def check(conn):
    results = {}

    # ── 基礎統計 ──────────────────────────────────────────
    total = conn.execute('SELECT COUNT(*) FROM land_transactions').fetchone()[0]
    results['total'] = total

    sn_null = conn.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE section_name IS NULL OR TRIM(section_name)=''"
    ).fetchone()[0]
    results['section_name_null'] = sn_null
    results['section_name_null_pct'] = round(sn_null / total * 100, 1) if total else 0

    loc_dihao = conn.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE location_raw LIKE '%地號%'"
    ).fetchone()[0]
    results['location_has_dihao'] = loc_dihao

    # ── Parse 失敗：含地號但 section_name NULL ──────────────
    parse_fail = conn.execute(
        "SELECT COUNT(*) FROM land_transactions "
        "WHERE (section_name IS NULL OR TRIM(section_name)='') AND location_raw LIKE '%地號%'"
    ).fetchone()[0]
    results['parse_fail'] = parse_fail
    results['parse_success_rate'] = round((loc_dihao - parse_fail) / loc_dihao * 100, 1) if loc_dihao else 100.0

    # ── 門牌誤判：section_name 含路名 ──────────────────────
    sn_road_rows = conn.execute(
        "SELECT rowid, section_name, location_raw FROM land_transactions WHERE section_name IS NOT NULL"
    ).fetchall()
    road_misclass = [(r, sn, loc) for r, sn, loc in sn_road_rows
                     if sn and ROAD_PAT.search(sn) and '地號' not in (loc or '')]
    results['road_misclassified'] = len(road_misclass)
    results['road_misclassified_sample'] = [(sn, loc) for _, sn, loc in road_misclass[:5]]

    # ── 縣市前綴污染 ──────────────────────────────────────
    county_prefix = [(r, sn, loc) for r, sn, loc in sn_road_rows
                     if sn and COUNTY_PREFIX_PAT.match(sn)]
    results['county_prefix_pollution'] = len(county_prefix)

    # ── PUA 字元 section_name ─────────────────────────────
    pua_sn = [(r, sn) for r, sn, _ in sn_road_rows if sn and PUA_PAT.search(sn)]
    results['pua_section_name'] = len(pua_sn)

    # ── 真正重複：raw_json 完全相同，或 record_id 相同且不為 NULL ──
    real_dup_rows = conn.execute("""
        SELECT COALESCE(SUM(c - 1), 0) FROM (
            SELECT raw_json, COUNT(*) c
            FROM land_transactions
            WHERE raw_json IS NOT NULL AND raw_json != ''
            GROUP BY raw_json
            HAVING c > 1
        )
    """).fetchone()[0]
    real_dup_by_rid = conn.execute("""
        SELECT COALESCE(SUM(c - 1), 0) FROM (
            SELECT record_id, COUNT(*) c
            FROM land_transactions
            WHERE record_id IS NOT NULL
            GROUP BY record_id
            HAVING c > 1
        )
    """).fetchone()[0]
    results['real_dup_extra_rows'] = real_dup_rows + real_dup_by_rid

    # ── 合法共有交易候選：business key 相同，但 record_id 不同、raw_json 不同 ──
    coown_groups = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT location_raw, trade_date, area_sqm, total_price, COUNT(*) c
            FROM land_transactions
            GROUP BY location_raw, trade_date, area_sqm, total_price
            HAVING c > 1
        )
    """).fetchone()[0]
    results['coown_candidate_groups'] = coown_groups

    # ── 異常日期 ─────────────────────────────────────────
    bad_dates = conn.execute(
        "SELECT trade_date, COUNT(*) FROM land_transactions "
        "WHERE trade_date > '2100-01-01' OR trade_date < '1990-01-01' "
        "GROUP BY trade_date ORDER BY trade_date DESC LIMIT 10"
    ).fetchall()
    results['bad_dates'] = bad_dates

    # ── land_details 統計 ─────────────────────────────────
    ld_total = conn.execute('SELECT COUNT(*) FROM land_details').fetchone()[0]
    ld_orphan = conn.execute("""
        SELECT COUNT(*) FROM land_details ld
        WHERE NOT EXISTS (SELECT 1 FROM land_transactions lt WHERE lt.record_id = ld.record_id)
    """).fetchone()[0]
    results['land_details_total'] = ld_total
    results['land_details_orphan'] = ld_orphan

    # ── 查詢覆蓋率（section_name 不含 NULL 的比率） ───────
    sale_total = conn.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE source_kind='sale'"
    ).fetchone()[0]
    sale_land = conn.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE source_kind='sale' AND transaction_target LIKE '%土地%'"
    ).fetchone()[0]
    sale_land_with_sn = conn.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE source_kind='sale' "
        "AND transaction_target LIKE '%土地%' AND section_name IS NOT NULL AND TRIM(section_name)!=''"
    ).fetchone()[0]
    results['sale_total'] = sale_total
    results['sale_land_total'] = sale_land
    results['sale_land_sn_coverage'] = round(sale_land_with_sn / sale_land * 100, 1) if sale_land else 0

    return results


def print_report(r):
    sep = '─' * 50
    print(sep)
    print('  LAND 資料品質報告')
    print(sep)
    print(f'總筆數:              {r["total"]:,}')
    print(f'  ├ 買賣:            {r["sale_total"]:,}')
    print(f'  ├ 土地買賣:        {r["sale_land_total"]:,}')
    print(f'  └ 土地 section 覆蓋率: {r["sale_land_sn_coverage"]}%')
    print()
    print(f'section_name NULL:   {r["section_name_null"]:,}  ({r["section_name_null_pct"]}%)')
    print(f'location_raw 含地號: {r["location_has_dihao"]:,}')
    print(f'Parse 失敗筆數:      {r["parse_fail"]}')
    print(f'Parse 成功率:        {r["parse_success_rate"]}%')
    print()
    print(f'門牌路名誤判 sn:     {r["road_misclassified"]}')
    if r['road_misclassified_sample']:
        for sn, loc in r['road_misclassified_sample']:
            print(f'  → {repr(sn)} | {repr((loc or "")[:50])}')
    print(f'縣市前綴污染 sn:     {r["county_prefix_pollution"]}')
    print(f'PUA 字元 sn:         {r["pua_section_name"]}  (正常，保留原始值)')
    print()
    print(f'真正重複匯入:        {r["real_dup_extra_rows"]:,} 筆多餘  (raw_json 相同 或 record_id 重複)')
    print(f'合法共有交易候選:    {r["coown_candidate_groups"]:,} 組  (業務鍵相同但 record_id/raw_json 不同，不計為錯誤)')
    print(f'land_details 總筆數: {r["land_details_total"]:,}  (孤立: {r["land_details_orphan"]:,})')
    print()
    if r['bad_dates']:
        print(f'異常日期 ({len(r["bad_dates"])}筆):')
        for d, c in r['bad_dates']:
            print(f'  {d}: {c}筆')
    else:
        print('異常日期: 無')
    print(sep)

    # ── 問題等級 ──
    issues = []
    if r['parse_fail'] > 0:
        issues.append(f'[嚴重] Parse 失敗 {r["parse_fail"]} 筆')
    if r['road_misclassified'] > 0:
        issues.append(f'[警告] 路名誤判 section_name {r["road_misclassified"]} 筆')
    if r['county_prefix_pollution'] > 0:
        issues.append(f'[警告] 縣市前綴污染 {r["county_prefix_pollution"]} 筆')
    if r['real_dup_extra_rows'] > 0:
        issues.append(f'[警告] 真正重複匯入：{r["real_dup_extra_rows"]} 筆多餘')
    if r['bad_dates']:
        issues.append(f'[警告] 異常日期 {sum(c for _, c in r["bad_dates"])} 筆')
    if r['land_details_orphan'] > 0:
        issues.append(f'[資訊] land_details 孤立筆數 {r["land_details_orphan"]}')

    if issues:
        print('待處理問題：')
        for i in issues:
            print(f'  {i}')
    else:
        print('無待處理問題。')
    print(sep)


def search_term(conn, term):
    """確認某地段名稱存在於哪些欄位。"""
    print(f'\n=== 搜尋：{term} ===')
    for tbl, col in [
        ('land_transactions', 'section_name'),
        ('land_transactions', 'location_raw'),
        ('land_transactions', 'raw_json'),
        ('land_details', 'location_raw'),
        ('land_details', 'raw_json'),
    ]:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col} LIKE ?", (f'%{term}%',)).fetchone()[0]
            status = f'{n} 筆' if n else '無'
            print(f'  {tbl}.{col}: {status}')
        except Exception as e:
            print(f'  {tbl}.{col}: ERR {e}')


if __name__ == '__main__':
    args = sys.argv[1:]
    db_path = DB_PATH
    search_terms = []
    for arg in args:
        p = Path(arg)
        if p.suffix == '.db' and p.exists():
            db_path = p
        else:
            search_terms.append(arg)

    if not db_path.exists():
        print(f'DB not found: {db_path}')
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    r = check(conn)
    print_report(r)

    for term in search_terms:
        search_term(conn, term)

    conn.close()
