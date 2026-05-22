#!/usr/bin/env python3
"""
process_land_transcripts.py  v5.1
電傳 YAML → 解析 → 更新 SQLite land_master.db → 同步主清冊 Excel

用法：
  python3 scripts/process_land_transcripts.py             # 正式執行
  python3 scripts/process_land_transcripts.py --dry-run   # 只解析，不寫入，不移動檔案

資料夾：
  電傳待解析/   ← 放入 .yaml / .yaml.txt
  電傳已完成/   ← 成功後移入
  電傳錯誤/     ← 解析失敗移入

YAML 格式（最小範例）：
  縣市: 新北市
  地區: 林口區
  地段: 力行段
  地號: "595"
  所有人:
    - 姓名: 王大明
      統一編號遮罩: H122*****1
      統一編號完整: H122345671
      登記次序: "0001"
      登記日期: "114/05/28"
      登記原因: 買賣
      原因發生日期: "114/05/20"
      分子: "1"
      分母: "1"
      地址: 新北市林口區文化一路100號
  公告現值: 12000
  土地面積坪: 638.384
"""

import argparse
import hashlib
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / 'data' / 'database' / 'land_master.db'

INBOX_DIR    = Path('/Users/xiaomingyang/Desktop/excel土地資料維護/電傳待解析')
DONE_DIR     = Path('/Users/xiaomingyang/Desktop/excel土地資料維護/電傳已完成')
ERROR_DIR    = Path('/Users/xiaomingyang/Desktop/excel土地資料維護/電傳錯誤')
EXCEL_DIR    = Path('/Users/xiaomingyang/Desktop/excel土地資料維護')
EXCEL_MASTER = EXCEL_DIR / '土地主清冊_正式版_20260522_郵遞區號補正版.xlsx'

# Excel 欄位索引（0-based），與主清冊定版一致
_XC = {
    '更新日期': 0, '分區': 1, '位置': 2, '縣市': 3, '地區': 4,
    '地段': 5, '地號': 6, '公告現值': 7, '次序': 8, '登記日期': 9,
    '登記原因': 10, '發生日期': 11, '所有權人': 12,
    '統一編號（遮罩）': 13, '統一編號（完整）': 14, '郵遞區號': 15,
    '住址': 16, '已售出': 17, '分母': 18, '分子': 19,
    '土地總坪數': 20, '權利範圍': 21, '備註': 22, '電話': 23,
}

# ── 共有人名單差異比對 ──────────────────────────────────────────────────────────

def _id_value(rec: dict) -> str:
    """取 ID 純值（不含欄位前綴），用於跨欄比對。優先：完整 > 遮罩。"""
    full = str(rec.get('owner_id_full') or '').strip().upper()
    if full and full not in ('', 'NONE'):
        return full
    masked = str(rec.get('owner_id_masked') or '').strip().upper()
    if masked and masked not in ('', 'NONE'):
        return masked
    return ''


def _owner_id(rec: dict) -> str:
    """
    產生比對 key。
    規則：兩邊只要 ID 純值相同即配對（不分完整/遮罩欄），ID 都空才退回姓名。
    """
    val = _id_value(rec)
    if val:
        return f'ID:{val}'
    name = str(rec.get('owner_name') or '').strip()
    return f'NAME:{name}'


def _share_str(rec: dict) -> str:
    n = str(rec.get('share_numer') or '').strip()
    d = str(rec.get('share_denom') or '').strip()
    if n and d:
        return f'{n}/{d}'
    return ''


def diff_owners(new_recs: list[dict], old_recs: list[dict]) -> dict:
    """
    共有人名單差異比對（名單 diff，不是整筆地號 sold）。
    new_recs : 電傳中的所有人列表
    old_recs : 同地號現有地主列表

    回傳：
      disappeared  : list[old_rec]    舊有新無 → 標已售出
      added        : list[new_rec]    新有舊無 → 插入新列
      unchanged    : list[(old, new)] 兩邊皆有，持分相同
      share_changed: list[(old, new)] 兩邊皆有，持分不同 → 備註持分異動
    """
    new_by_id = {_owner_id(r): r for r in new_recs}
    old_by_id = {_owner_id(r): r for r in old_recs}

    new_ids = set(new_by_id)
    old_ids = set(old_by_id)

    disappeared   = [old_by_id[i] for i in old_ids - new_ids]
    added         = [new_by_id[i] for i in new_ids - old_ids]
    unchanged     = []
    share_changed = []

    for i in old_ids & new_ids:
        old_r = old_by_id[i]
        new_r = new_by_id[i]
        if _share_str(old_r) and _share_str(new_r) and _share_str(old_r) != _share_str(new_r):
            share_changed.append((old_r, new_r))
        else:
            unchanged.append((old_r, new_r))

    return {
        'disappeared':   disappeared,
        'added':         added,
        'unchanged':     unchanged,
        'share_changed': share_changed,
    }


# ── 與 import_land_master.py 保持完全一致的 key 函數 ─────────────────────────

def normalize_section(raw):
    if not raw: return ''
    s = str(raw).strip()
    s = re.sub(r'[\(（][^)\）]*[\)）]', '', s)
    return re.sub(r'\s+', '', s)


def normalize_land_no(raw):
    if not raw: return ''
    s = str(raw).strip()
    s = re.sub(r'之', '-', s)
    s = re.sub(r'[^\d\-]', '', s)
    if not s: return ''
    parts = s.split('-')
    try:
        main = int(parts[0]) if parts[0] else 0
        sub  = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        return f'{main:04d}-{sub:04d}'
    except ValueError:
        return s


def make_land_match_key(city, district, norm_sec, norm_no):
    return '_'.join(s.strip() for s in [city, district, norm_sec, norm_no] if s.strip())


def make_owner_key(owner_id_full, owner_name, owner_id_masked):
    if owner_id_full and str(owner_id_full).strip():
        src = str(owner_id_full).strip().upper()
    else:
        name   = (owner_name   or '').strip()
        masked = (owner_id_masked or '').strip()
        src    = f'{name}|{masked}'
    if not src or src == '|': return ''
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def make_event_key(rec):
    parts = [
        rec.get('land_match_key') or '',
        rec.get('owner_key')      or '',
        str(rec.get('reg_seq')    or ''),
        str(rec.get('reg_date')   or ''),
        str(rec.get('reg_reason') or ''),
        str(rec.get('share_numer') or ''),
        str(rec.get('share_denom') or ''),
    ]
    if not any(parts[:2]): return ''
    raw = '|'.join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def calc_actual_area(total, numer, denom):
    try:
        t, n, d = float(total), float(numer), float(denom)
        return round(t * n / d, 4) if d else None
    except (TypeError, ValueError):
        return None


# ── YAML 解析 ────────────────────────────────────────────────────────────────

class ParseError(Exception):
    pass


def parse_transcript(path: Path) -> list[dict]:
    """
    解析一份電傳 YAML，回傳 list of record dict（每位所有人一筆）。
    若格式錯誤 raise ParseError。
    """
    try:
        text = path.read_text(encoding='utf-8')
        data = yaml.safe_load(text)
    except Exception as e:
        raise ParseError(f'YAML 讀取失敗：{e}')

    if not isinstance(data, dict):
        raise ParseError('YAML 根層級必須是 dict')

    city      = str(data.get('縣市') or '').strip()
    district  = str(data.get('地區') or '').strip()
    sec_raw   = str(data.get('地段') or '').strip()
    no_raw    = str(data.get('地號') or '').strip()
    ann_val   = data.get('公告現值')
    total_area= data.get('土地面積坪')

    if not city or not district or not sec_raw or not no_raw:
        raise ParseError(f'缺少必要欄位（縣市/地區/地段/地號）：{dict(縣市=city,地區=district,地段=sec_raw,地號=no_raw)}')

    owners = data.get('所有人') or []
    if not isinstance(owners, list) or len(owners) == 0:
        raise ParseError('所有人 欄位必須為非空 list')

    norm_sec = normalize_section(sec_raw)
    norm_no  = normalize_land_no(no_raw)
    lmk      = make_land_match_key(city, district, norm_sec, norm_no)

    records = []
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for owner_data in owners:
        if not isinstance(owner_data, dict):
            raise ParseError(f'所有人 項目格式錯誤：{owner_data!r}')

        name        = str(owner_data.get('姓名') or '').strip()
        masked      = str(owner_data.get('統一編號遮罩') or '').strip()
        full_id     = str(owner_data.get('統一編號完整') or '').strip()
        reg_seq     = str(owner_data.get('登記次序') or '').strip()
        reg_date    = str(owner_data.get('登記日期') or '').strip()
        reg_reason  = str(owner_data.get('登記原因') or '').strip()
        cause_date  = str(owner_data.get('原因發生日期') or '').strip()
        share_numer = str(owner_data.get('分子') or '').strip()
        share_denom = str(owner_data.get('分母') or '').strip()
        address     = str(owner_data.get('地址') or '').strip()
        postal      = str(owner_data.get('郵遞區號') or '').strip()
        phone       = str(owner_data.get('電話') or '').strip()
        note        = str(owner_data.get('備註') or '').strip()

        if not name:
            raise ParseError(f'所有人缺少姓名：{owner_data!r}')

        owner_key = make_owner_key(full_id or None, name, masked or None)
        actual    = calc_actual_area(total_area, share_numer, share_denom)

        rec = {
            # 地籍
            'city':              city,
            'district':          district,
            'section_raw':       sec_raw,
            'land_no_raw':       no_raw,
            'announced_value':   ann_val,
            'total_area_ping':   total_area,
            # 登記事件
            'reg_seq':           reg_seq or None,
            'reg_date':          reg_date or None,
            'cause_date':        cause_date or None,
            'reg_reason':        reg_reason or None,
            # 所有人
            'owner_name':        name,
            'owner_id_masked':   masked or None,
            'owner_id_full':     full_id or None,
            'postal_code':       postal or None,
            'address':           address or None,
            'phone':             phone or None,
            'share_numer':       share_numer or None,
            'share_denom':       share_denom or None,
            'note':              note or None,
            # Python 生成
            'normalized_section': norm_sec,
            'normalized_land_no': norm_no,
            'land_match_key':    lmk,
            'owner_key':         owner_key,
            'actual_owned_area': actual,
            # 系統
            'updated_at':        now_str,
            'imported_at':       now_str,
        }
        rec['event_key'] = make_event_key(rec)
        records.append(rec)

    return records


# ── SQLite DDL（log 表）────────────────────────────────────────────────────

DDL_LOG = """
CREATE TABLE IF NOT EXISTS transcript_import_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT,
    event_key   TEXT,
    owner_name  TEXT,
    land_key    TEXT,
    reg_reason  TEXT,
    status      TEXT,       -- inserted / skipped / sold_marked / error
    error_msg   TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
"""

INSERT_SQL = """
INSERT INTO land_master (
    updated_at, city, district, section_raw, land_no_raw,
    announced_value, total_area_ping,
    reg_seq, reg_date, cause_date, reg_reason,
    owner_name, owner_id_masked, owner_id_full,
    postal_code, address, phone,
    share_numer, share_denom, note,
    normalized_section, normalized_land_no,
    land_match_key, owner_key, event_key,
    actual_owned_area, imported_at,
    is_sold, realprice_match_status
) VALUES (
    :updated_at, :city, :district, :section_raw, :land_no_raw,
    :announced_value, :total_area_ping,
    :reg_seq, :reg_date, :cause_date, :reg_reason,
    :owner_name, :owner_id_masked, :owner_id_full,
    :postal_code, :address, :phone,
    :share_numer, :share_denom, :note,
    :normalized_section, :normalized_land_no,
    :land_match_key, :owner_key, :event_key,
    :actual_owned_area, :imported_at,
    0, 'pending'
)
"""


# ── Telegram 推播 ─────────────────────────────────────────────────────────────

def _send_telegram(text: str):
    import urllib.request, urllib.parse, json
    from pathlib import Path as _P
    env_path = _P(__file__).resolve().parent.parent / '.env'
    token = chat_id = ''
    for line in env_path.read_text().splitlines():
        if line.startswith('TELEGRAM_BOT_TOKEN='):
            token = line.split('=', 1)[1].strip()
        if line.startswith('TELEGRAM_CHAT_ID='):
            chat_id = line.split('=', 1)[1].strip()
    if not token or not chat_id:
        print('  [TG] .env 缺少 token/chat_id，略過推播')
        return
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = json.dumps({'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'  [TG] 推播失敗：{e}')


def build_tg_message(filename: str, records: list[dict],
                     inserted: list[dict], sold_marked: int) -> str:
    lines = [f'✅ 電傳解析完成\n📁 {filename}']
    for rec in inserted:
        lines.append(
            f'\n📍 {rec["section_raw"]} {rec["land_no_raw"]}'
            f'\n👤 新地主：{rec["owner_name"]}'
            f'\n📄 登記原因：{rec.get("reg_reason") or "—"}'
            f'\n📅 登記日期：{rec.get("reg_date") or "—"}'
        )
    if sold_marked:
        lines.append(f'\n🏷 已標記 {sold_marked} 筆舊地主為已售出')
    return '\n'.join(lines)


# ── Excel 主清冊同步 ─────────────────────────────────────────────────────────

def sync_excel(all_inserted: list[dict], dry_run: bool) -> dict:
    """
    同步更新主清冊 Excel。正確邏輯：

    新地主（買賣）：
      1. 找同地段地號的所有舊地主列
         → 已售出=1，備註追加「依新電傳確認移轉，已新增新地主列」，更新日期=今日
      2. 在「最後一筆舊地主列正下方」插入新地主列（insert_rows）
         → 已售出=0，更新日期=今日，填入電傳資料

    非買賣 / 新地主本人已在清冊：
      → 只更新次序/登記日期/登記原因/發生日期/更新日期，不新增列

    覆寫同一個正式主清冊檔案。
    回傳 {sold_rows, inserted_rows, updated_rows, preview, excel_path}
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    if not all_inserted:
        return {'sold_rows': 0, 'inserted_rows': 0, 'updated_rows': 0,
                'preview': [], 'excel_path': str(EXCEL_MASTER)}

    today = datetime.now().strftime('%Y/%m/%d')

    print(f'\n[Excel 同步] 讀取主清冊...')
    wb = openpyxl.load_workbook(str(EXCEL_MASTER), data_only=True)
    ws = wb.active

    # 建立 (norm_sec, norm_no) → [row_1based, ...] 排序後的列表
    # 以及 (norm_sec, norm_no, owner_name) → row_1based
    land_rows:  dict[tuple, list[int]] = {}
    owner_rows: dict[tuple, int]       = {}

    total_data_rows = ws.max_row - 1
    print(f'  {total_data_rows:,} 資料列，建立索引中...')

    for r in range(2, ws.max_row + 1):
        sec_v = ws.cell(r, _XC['地段'] + 1).value
        no_v  = ws.cell(r, _XC['地號'] + 1).value
        name  = str(ws.cell(r, _XC['所有權人'] + 1).value or '').strip()
        n_sec = normalize_section(str(sec_v or ''))
        n_no  = normalize_land_no(str(no_v or ''))
        lk = (n_sec, n_no)
        land_rows.setdefault(lk, []).append(r)
        if name:
            owner_rows[(n_sec, n_no, name)] = r

    DATA_FONT = Font(name='微軟正黑體', size=10)
    SOLD_FILL = PatternFill('solid', fgColor='FFD7D7')   # 淡紅：已售出
    NEW_FILL  = PatternFill('solid', fgColor='E2EFDA')   # 淡綠：新增列

    sold_rows     = 0
    inserted_rows = 0
    updated_rows  = 0
    share_chg_rows= 0
    preview       = []

    # ── 按地號分組，收集同地號所有新地主，統一做 diff ──
    # all_inserted 可能含同一地號的多筆新地主（一份電傳可有多共有人）
    from collections import defaultdict
    by_land: dict[tuple, list[dict]] = defaultdict(list)
    for rec in all_inserted:
        lk = (rec['normalized_section'], rec['normalized_land_no'])
        by_land[lk].append(rec)

    # ops: list of (action, target_row_or_None, list[old_rows], rec)
    ops = []

    for lk, new_recs in by_land.items():
        n_sec, n_no = lk
        all_land_rows = land_rows.get(lk, [])

        # 把 Excel 舊地主整理成 dict 格式供 diff 使用
        old_recs_excel = []
        for r in all_land_rows:
            old_recs_excel.append({
                '_row':          r,
                'owner_name':    str(ws.cell(r, _XC['所有權人']       + 1).value or '').strip(),
                'owner_id_full': str(ws.cell(r, _XC['統一編號（完整）'] + 1).value or '').strip(),
                'owner_id_masked': str(ws.cell(r, _XC['統一編號（遮罩）'] + 1).value or '').strip(),
                'share_numer':   str(ws.cell(r, _XC['分子']           + 1).value or '').strip(),
                'share_denom':   str(ws.cell(r, _XC['分母']           + 1).value or '').strip(),
            })

        result = diff_owners(new_recs, old_recs_excel)

        # 判定插入位置：同地號 Excel 列中最大 row
        insert_after = max(all_land_rows) if all_land_rows else None
        land_label   = f'{new_recs[0]["section_raw"]} {new_recs[0]["land_no_raw"]}'

        # A. 消失的地主 → 標已售出
        for old_r in result['disappeared']:
            row = old_r['_row']
            ops.append(('mark_sold', row, old_r, None))
            sold_rows += 1
            preview.append(f'  ❌ 標已售出  {land_label}  row={row}  {old_r["owner_name"]}'
                           f'  （{_owner_id(old_r)}）')

        # B. 持分改變
        for old_r, new_r in result['share_changed']:
            row = old_r['_row']
            ops.append(('mark_share_changed', row, old_r, new_r))
            share_chg_rows += 1
            preview.append(f'  ⚠️  持分異動  {land_label}  row={row}  {old_r["owner_name"]}'
                           f'  {_share_str(old_r)} → {_share_str(new_r)}  備註待確認')

        # C. 未變動地主
        for old_r, new_r in result['unchanged']:
            preview.append(f'  ✅ 保持持有  {land_label}  row={old_r["_row"]}  {old_r["owner_name"]}'
                           f'  持分={_share_str(old_r)}')

        # D. 新增地主 → 插入在最後一筆下方
        for new_r in result['added']:
            ops.append(('insert_new', insert_after, None, new_r))
            inserted_rows += 1
            insert_pos = (insert_after + 1) if insert_after else '末尾'
            preview.append(f'  ➕ 新增地主  {land_label}  插入 row={insert_pos}  '
                           f'{new_r["owner_name"]}  持分={_share_str(new_r)}  '
                           f'event_key={new_r.get("event_key","")[:12]}...')
            # 確認下一個新增地主插入位置遞增
            if insert_after is not None:
                insert_after += 1

    # ── dry-run：只印不執行 ──
    if dry_run:
        print(f'\n  [dry-run] 共有人 diff 結果：')
        for line in preview:
            print(f'  {line}')
        print(f'\n  [dry-run] 摘要：標記已售出={sold_rows}  持分異動={share_chg_rows}'
              f'  插入新地主={inserted_rows}（未寫入）')
        wb.close()
        return {'sold_rows': sold_rows, 'inserted_rows': inserted_rows,
                'updated_rows': updated_rows, 'share_chg_rows': share_chg_rows,
                'preview': preview, 'excel_path': str(EXCEL_MASTER)}

    # ── 正式執行：先倒序 insert，再 mark_sold，最後 mark_share_changed ──
    def _build_new_row_data(rec: dict) -> dict:
        return {
            _XC['更新日期']:          today,
            _XC['縣市']:              rec.get('city'),
            _XC['地區']:              rec.get('district'),
            _XC['地段']:              rec.get('section_raw'),
            _XC['地號']:              rec.get('land_no_raw'),
            _XC['公告現值']:          rec.get('announced_value'),
            _XC['次序']:              rec.get('reg_seq'),
            _XC['登記日期']:          rec.get('reg_date'),
            _XC['登記原因']:          rec.get('reg_reason'),
            _XC['發生日期']:          rec.get('cause_date'),
            _XC['所有權人']:          rec.get('owner_name'),
            _XC['統一編號（遮罩）']:  rec.get('owner_id_masked'),
            _XC['統一編號（完整）']:  rec.get('owner_id_full'),
            _XC['郵遞區號']:          rec.get('postal_code'),
            _XC['住址']:              rec.get('address'),
            _XC['已售出']:            0,
            _XC['分母']:              rec.get('share_denom'),
            _XC['分子']:              rec.get('share_numer'),
            _XC['土地總坪數']:        rec.get('total_area_ping'),
            _XC['電話']:              rec.get('phone'),
            _XC['備註']:              rec.get('note'),
        }

    # insert_new：倒序（避免行號偏移）
    insert_ops = [(op, tgt, old, rec) for op, tgt, old, rec in ops if op == 'insert_new']
    for op, insert_after, _, rec in sorted(insert_ops, key=lambda x: -(x[1] or 0)):
        nr = (insert_after + 1) if insert_after is not None else ws.max_row + 1
        if insert_after is not None:
            ws.insert_rows(nr)
        data = _build_new_row_data(rec)
        for ci, val in data.items():
            cell = ws.cell(nr, ci + 1)
            cell.value = val
            cell.font  = DATA_FONT
            cell.fill  = NEW_FILL
        for col_name in ('地號', '統一編號（遮罩）', '統一編號（完整）', '郵遞區號', '電話'):
            ws.cell(nr, _XC[col_name] + 1).number_format = '@'

    # mark_sold
    for op, tgt, old_r, _ in ops:
        if op == 'mark_sold':
            old_note = str(ws.cell(tgt, _XC['備註'] + 1).value or '').strip()
            new_note = f'{old_note}｜依電傳確認移轉({today})已新增新地主'.lstrip('｜')
            ws.cell(tgt, _XC['已售出']   + 1).value = 1
            ws.cell(tgt, _XC['備註']     + 1).value = new_note
            ws.cell(tgt, _XC['更新日期'] + 1).value = today
            ws.cell(tgt, _XC['已售出']   + 1).fill  = SOLD_FILL

    # mark_share_changed
    SHARE_CHG_FILL = PatternFill('solid', fgColor='FFF2CC')
    for op, tgt, old_r, new_r in ops:
        if op == 'mark_share_changed':
            old_note = str(ws.cell(tgt, _XC['備註'] + 1).value or '').strip()
            note_add = f'持分異動待確認({today})：{_share_str(old_r)}→{_share_str(new_r)}'
            ws.cell(tgt, _XC['備註']     + 1).value = f'{old_note}｜{note_add}'.lstrip('｜')
            ws.cell(tgt, _XC['更新日期'] + 1).value = today
            ws.cell(tgt, _XC['備註']     + 1).fill  = SHARE_CHG_FILL

    print(f'  儲存中...')
    wb.save(str(EXCEL_MASTER))
    wb.close()
    print(f'  已儲存：{EXCEL_MASTER}')

    return {'sold_rows': sold_rows, 'inserted_rows': inserted_rows,
            'updated_rows': updated_rows, 'share_chg_rows': share_chg_rows,
            'preview': preview, 'excel_path': str(EXCEL_MASTER)}


# ── 主流程 ──────────────────────────────────────────────────────────────────

def process_file(path: Path, con: sqlite3.Connection,
                 dry_run: bool) -> dict:
    """
    處理單一電傳檔案。
    回傳 {status, records, inserted, sold_marked, error}
    """
    result = {'status': 'ok', 'records': [], 'inserted': [], 'sold_marked': 0, 'error': ''}

    try:
        records = parse_transcript(path)
    except ParseError as e:
        result['status'] = 'error'
        result['error']  = str(e)
        return result

    result['records'] = records
    cur = con.cursor()

    for rec in records:
        ek = rec['event_key']
        if not ek:
            result['error'] += f'owner={rec["owner_name"]} event_key 為空（跳過）; '
            continue

        # 是否已存在
        exists = cur.execute('SELECT id FROM land_master WHERE event_key=?', (ek,)).fetchone()
        if exists:
            if not dry_run:
                cur.execute(
                    "INSERT INTO transcript_import_log (filename,event_key,owner_name,land_key,reg_reason,status) VALUES (?,?,?,?,?,?)",
                    (path.name, ek, rec['owner_name'], rec['land_match_key'], rec.get('reg_reason'), 'skipped')
                )
            continue

        # 新增
        if not dry_run:
            cur.execute(INSERT_SQL, rec)
            cur.execute(
                "INSERT INTO transcript_import_log (filename,event_key,owner_name,land_key,reg_reason,status) VALUES (?,?,?,?,?,?)",
                (path.name, ek, rec['owner_name'], rec['land_match_key'], rec.get('reg_reason'), 'inserted')
            )
        result['inserted'].append(rec)

    # ── SQLite 共有人 diff：依新電傳名單，標記消失的地主 is_sold=1 ──
    if not dry_run:
        from collections import defaultdict
        by_land_new: dict[str, list[dict]] = defaultdict(list)
        for rec in result['inserted']:
            by_land_new[rec['land_match_key']].append(rec)

        for lmk, new_recs in by_land_new.items():
            # 取 DB 現有地主（is_sold=0）
            old_db = cur.execute(
                """SELECT owner_key, owner_name, owner_id_masked, owner_id_full,
                          share_numer, share_denom
                   FROM land_master
                   WHERE land_match_key=? AND is_sold=0""",
                (lmk,)
            ).fetchall()
            old_recs_db = [
                {'owner_key': r[0], 'owner_name': r[1],
                 'owner_id_masked': r[2], 'owner_id_full': r[3],
                 'share_numer': str(r[4] or ''), 'share_denom': str(r[5] or '')}
                for r in old_db
            ]
            diff = diff_owners(new_recs, old_recs_db)

            # 消失的地主 → is_sold=1
            for old_r in diff['disappeared']:
                now_str = result['inserted'][0]['updated_at']
                updated = cur.execute(
                    """UPDATE land_master SET is_sold=1, updated_at=?
                       WHERE land_match_key=? AND owner_key=? AND is_sold=0""",
                    (now_str, lmk, old_r['owner_key'])
                ).rowcount
                result['sold_marked'] += updated
                if updated:
                    cur.execute(
                        "INSERT INTO transcript_import_log "
                        "(filename,event_key,owner_name,land_key,reg_reason,status) VALUES (?,?,?,?,?,?)",
                        (path.name, '', old_r['owner_name'], lmk, '共有人消失→標已售出', 'sold_marked')
                    )

    if not dry_run:
        con.commit()

    return result


def main():
    ap = argparse.ArgumentParser(description='電傳 YAML → SQLite')
    ap.add_argument('--dry-run', action='store_true', help='只解析，不寫入，不移動檔案')
    ap.add_argument('--db', default=str(DB_PATH))
    args = ap.parse_args()

    dry_run = args.dry_run
    mode_label = '【DRY-RUN】' if dry_run else '【正式執行】'
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'{mode_label} process_land_transcripts.py')
    print(f'時間：{ts}')
    print(f'DB  ：{args.db}')
    print(f'收件：{INBOX_DIR}')

    # 建立 log 表
    con = sqlite3.connect(args.db)
    con.execute(DDL_LOG)
    con.commit()

    # 掃描待解析資料夾
    files = sorted(
        p for p in INBOX_DIR.iterdir()
        if p.is_file() and (p.suffix in ('.yaml', '.txt') or p.name.endswith('.yaml.txt'))
    )
    print(f'\n掃描到 {len(files)} 個電傳檔案')
    if not files:
        print('  無待解析檔案，結束。')
        con.close()
        return

    total_inserted   = 0
    total_sold       = 0
    total_skipped    = 0
    total_error      = 0
    dry_run_samples  = []
    all_inserted_recs = []   # 收集所有成功插入的 rec，供 Excel 同步用

    for f in files:
        print(f'\n── {f.name} ──')
        result = process_file(f, con, dry_run=dry_run)

        n_rec     = len(result['records'])
        n_insert  = len(result['inserted'])
        n_sold    = result['sold_marked']
        # 跳過 = 解析成功但 event_key 已存在
        n_skipped = n_rec - n_insert if result['status'] == 'ok' else 0

        if result['status'] == 'error':
            total_error += 1
            print(f'  ❌ 解析失敗：{result["error"]}')
            if not dry_run:
                dest = ERROR_DIR / f.name
                shutil.move(str(f), str(dest))
                # 寫入 error log
                con.execute(
                    "INSERT INTO transcript_import_log (filename,status,error_msg) VALUES (?,?,?)",
                    (f.name, 'error', result['error'])
                )
                con.commit()
                # 寫 .error 文字檔
                (ERROR_DIR / (f.name + '.error')).write_text(
                    f'{ts}\n{result["error"]}\n', encoding='utf-8'
                )
                print(f'  → 移至 電傳錯誤/')
        else:
            total_inserted += n_insert
            total_sold     += n_sold
            total_skipped  += n_skipped
            print(f'  解析筆數：{n_rec}　新增：{n_insert}　SKIP：{n_skipped}　標記已售出：{n_sold}')
            for rec in result['inserted'][:3]:
                print(f'    ✅ [{rec["reg_reason"] or "—"}] {rec["section_raw"]} {rec["land_no_raw"]} → {rec["owner_name"]}')

            if dry_run:
                dry_run_samples.extend(result['records'][:5])
            else:
                all_inserted_recs.extend(result['inserted'])
                # 移到已完成
                dest = DONE_DIR / f.name
                if dest.exists():
                    dest = DONE_DIR / f'{f.stem}_{ts.replace(":","").replace(" ","_")}{f.suffix}'
                shutil.move(str(f), str(dest))
                print(f'  → 移至 電傳已完成/')

                # Telegram
                if result['inserted']:
                    msg = build_tg_message(f.name, result['records'],
                                           result['inserted'], n_sold)
                    _send_telegram(msg)
                    print(f'  [TG] 摘要已推播')

    con.close()

    # ── Excel 同步（所有檔案處理完後一次執行）──
    excel_result = {'sold_rows': 0, 'updated_rows': 0, 'added_rows': 0}
    if all_inserted_recs or dry_run:
        recs_for_sync = all_inserted_recs if not dry_run else (dry_run_samples or [])
        if recs_for_sync or all_inserted_recs:
            excel_result = sync_excel(
                all_inserted_recs if not dry_run else dry_run_samples,
                dry_run=dry_run
            )

    print(f'\n{"─"*50}')
    if dry_run:
        print(f'{mode_label} 摘要')
        print(f'  可解析檔數          ：{len(files) - total_error}')
        print(f'  可新增 SQLite 事件  ：{total_inserted}')
        print(f'  SKIP（已存在）      ：{total_skipped}')
        print(f'  會標記已售出（SQLite）：（dry-run 不執行比對）')
        print(f'  解析失敗            ：{total_error}')
        print(f'\nExcel 同步預覽（dry-run）：')
        print(f'  消失地主→標已售出  ：{excel_result["sold_rows"]}')
        print(f'  插入新地主列        ：{excel_result["inserted_rows"]}')
        print(f'  持分異動→備註       ：{excel_result.get("share_chg_rows",0)}')
        print(f'  現有列保持不變      ：（見上方 diff 明細）')
        print(f'\n檔案移動規則（dry-run 不執行）：')
        print(f'  成功 → {DONE_DIR}')
        print(f'  失敗 → {ERROR_DIR}  + .error 說明檔')
        if dry_run_samples:
            print(f'\n前 {min(5,len(dry_run_samples))} 筆解析案例：')
            for i, rec in enumerate(dry_run_samples[:5], 1):
                print(f'  [{i}] {rec["city"]}{rec["district"]} {rec["section_raw"]} {rec["land_no_raw"]}'
                      f' / {rec["owner_name"]} / {rec.get("reg_reason","—")} / {rec.get("reg_date","—")}'
                      f' / event_key={rec["event_key"][:8]}...')
    else:
        print(f'完成')
        print(f'SQLite：')
        print(f'  實際新增事件        ：{total_inserted}')
        print(f'  SKIP（已存在）      ：{total_skipped}')
        print(f'  標記已售出          ：{total_sold}')
        print(f'  解析失敗            ：{total_error}')
        print(f'Excel 主清冊同步：')
        print(f'  消失地主→標已售出  ：{excel_result["sold_rows"]}')
        print(f'  插入新地主列        ：{excel_result["inserted_rows"]}')
        print(f'  持分異動→備註       ：{excel_result.get("share_chg_rows",0)}')
        print(f'  主清冊路徑          ：{excel_result["excel_path"]}')


if __name__ == '__main__':
    main()
