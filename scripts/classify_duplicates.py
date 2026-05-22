#!/usr/bin/env python3
"""
classify_duplicates.py
分析 land_transactions 重複組，分類為：
  A. 完全重複匯入 — 主要欄位 + raw_json 完全相同
  B. 合法共有交易 — record_id 不同，備註/權利人/序號不同
  C. 同一買賣多地號 — 同日期同總價但地號不同
  D. 無法判斷
不刪資料，只輸出報告。
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / 'db' / 'land_intel.db'

DUP_KEY = ('location_raw', 'trade_date', 'area_sqm', 'total_price')
SAMPLE_LIMIT = 20


def load_dup_groups(conn):
    rows = conn.execute("""
        SELECT location_raw, trade_date, area_sqm, total_price, COUNT(*) c
        FROM land_transactions
        GROUP BY location_raw, trade_date, area_sqm, total_price
        HAVING c > 1
        ORDER BY c DESC
    """).fetchall()
    return rows


def fetch_group(conn, location_raw, trade_date, area_sqm, total_price):
    return conn.execute("""
        SELECT id, record_id, unique_key, city, district, location_raw,
               trade_date, area_ping, total_price_wan, unit_price_per_ping_wan,
               transaction_target, note, raw_json, original_csv_row, source_file
        FROM land_transactions
        WHERE location_raw IS ? AND trade_date IS ? AND area_sqm IS ? AND total_price IS ?
        ORDER BY record_id
    """, (location_raw, trade_date, area_sqm, total_price)).fetchall()


COLS = ['id','record_id','unique_key','city','district','location_raw',
        'trade_date','area_ping','total_price_wan','unit_price_per_ping_wan',
        'transaction_target','note','raw_json','original_csv_row','source_file']


def classify_group(members):
    """
    Returns (category, reason)
    A: 完全重複匯入
    B: 合法共有交易
    C: 同一買賣多地號
    D: 無法判斷
    """
    rows = [dict(zip(COLS, m)) for m in members]

    # Check raw_json identity
    raw_jsons = [r['raw_json'] for r in rows]
    all_same_raw = len(set(raw_jsons)) == 1

    # Check record_ids
    record_ids = [r['record_id'] for r in rows]
    has_null_rid = any(rid is None for rid in record_ids)
    distinct_rids = set(rid for rid in record_ids if rid is not None)

    if all_same_raw:
        return 'A', f'raw_json 完全相同 ({len(rows)} 筆)'

    # Try parse raw_json for note/buyer fields
    def get_note(r):
        if r['note']:
            return r['note']
        try:
            j = json.loads(r['raw_json'] or '{}')
            return j.get('備註') or j.get('note') or ''
        except Exception:
            return ''

    def get_seq(r):
        # 交易序號 or similar
        try:
            j = json.loads(r['raw_json'] or '{}')
            return j.get('交易序號') or j.get('移轉層次') or ''
        except Exception:
            return ''

    notes = [get_note(r) for r in rows]
    seqs = [get_seq(r) for r in rows]
    distinct_notes = set(notes)
    distinct_seqs = set(seqs)

    # Check if location_raw differs (multi-lot same txn)
    loc_raws = [r['location_raw'] for r in rows]
    if len(set(loc_raws)) > 1:
        return 'C', f'地號不同 ({len(set(loc_raws))} 種 location_raw)'

    # Same location_raw; different record_ids → likely co-ownership
    if len(distinct_rids) == len(rows) and not has_null_rid:
        if len(distinct_notes) > 1 or len(distinct_seqs) > 1:
            return 'B', f'record_id 各異，備註/序號不同 → 合法共有'
        else:
            return 'B', f'record_id 各異 ({len(distinct_rids)} 個) → 疑合法共有，備註相同'

    if has_null_rid and len(distinct_rids) > 0:
        return 'D', f'混合 NULL + 有值 record_id → 無法確定'

    return 'D', f'record_ids={record_ids}'


def main():
    conn = sqlite3.connect(str(DB_PATH))
    groups = load_dup_groups(conn)
    total_extra = sum(g[4] - 1 for g in groups)

    print('=' * 60)
    print(f'  Duplicate Classification Report')
    print(f'  重複組總數: {len(groups)}  多餘筆數: {total_extra}')
    print('=' * 60)
    print()
    print(f'判斷欄位 (DUP_KEY): {DUP_KEY}')
    print()

    counts = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    extra  = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    sample_printed = 0

    all_classified = []
    for g in groups:
        location_raw, trade_date, area_sqm, total_price, cnt = g
        members = fetch_group(conn, location_raw, trade_date, area_sqm, total_price)
        cat, reason = classify_group(members)
        counts[cat] += 1
        extra[cat] += cnt - 1
        all_classified.append((cat, reason, members))

    print('── 分類結果 ─────────────────────────────────────────')
    for cat, label in [
        ('A', '完全重複匯入'),
        ('B', '合法共有交易'),
        ('C', '同一買賣多地號'),
        ('D', '無法判斷'),
    ]:
        print(f'  {cat}. {label}: {counts[cat]} 組  (多餘 {extra[cat]} 筆)')
    print()

    print(f'── 抽樣明細 (最多 {SAMPLE_LIMIT} 組) ──────────────────────')
    printed = 0
    for cat, reason, members in all_classified:
        if printed >= SAMPLE_LIMIT:
            break
        rows = [dict(zip(COLS, m)) for m in members]
        r0 = rows[0]
        print(f'\n[{cat}] {reason}')
        print(f'  城市: {r0["city"]} {r0["district"]}')
        print(f'  地點: {r0["location_raw"]}')
        print(f'  日期: {r0["trade_date"]}  坪: {r0["area_ping"]}  總價: {r0["total_price_wan"]}萬  單價: {r0["unit_price_per_ping_wan"]}萬/坪')
        print(f'  交易目標: {r0["transaction_target"]}')
        for i, row in enumerate(rows):
            raw_jsons = [r['raw_json'] for r in rows]
            same_json = '同' if len(set(raw_jsons)) == 1 else '不同'
            print(f'  [{i+1}] record_id={row["record_id"]}  note={repr((row["note"] or "")[:30])}  raw_json={same_json}  src={row["source_file"]}')
        printed += 1

    print()
    print('── 建議 ──────────────────────────────────────────────')
    if counts['A'] > 0:
        print(f'  A ({counts["A"]} 組, 多餘 {extra["A"]} 筆): 建議可安全刪除 — raw_json 完全相同')
    else:
        print(f'  A: 0 組 — 無完全重複匯入')
    if counts['B'] > 0:
        print(f'  B ({counts["B"]} 組, 多餘 {extra["B"]} 筆): 不可刪 — 合法共有交易，record_id 不同')
    if counts['C'] > 0:
        print(f'  C ({counts["C"]} 組, 多餘 {extra["C"]} 筆): 不可刪 — 同一買賣多地號，各筆地號不同')
    if counts['D'] > 0:
        print(f'  D ({counts["D"]} 組, 多餘 {extra["D"]} 筆): 不刪 — 資料不足，需人工確認')

    print()
    if counts['A'] == 0:
        print('→ validate_land_data.py 的 duplicate 判斷過於寬鬆：')
        print('  目前以 (location_raw, trade_date, area_sqm, area_ping) 為重複鍵，')
        print('  但合法共有交易本來就是相同欄位 + 不同 record_id。')
        print('  建議修改：只在 raw_json 完全相同 OR record_id 相同時才標為「重複」。')
    print('=' * 60)
    conn.close()


if __name__ == '__main__':
    main()
