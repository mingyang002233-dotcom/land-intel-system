#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
land_master_bot.py — 土地主清冊 Telegram 快速回填 bot

功能：
  /whoami          — 顯示自己的 Telegram user_id（用於加入白名單）
  /note 地號 備註   — 追加備註（append，不覆蓋）
  /phone 地號 電話  — 追加電話
  /sold 地號        — 標記已售出
  /query 關鍵字     — 查詢地號 / 所有人 / 電話 / 地址
  /history 地號     — 查詢同地號歷史登記事件
  /owner 姓名       — 查詢地主名下所有土地

環境變數（.env）：
  TELEGRAM_BOT_TOKEN        — bot token
  TELEGRAM_ALLOWED_USERS    — 白名單 user_id，逗號分隔，例如：123456789,987654321

啟動：
  python3 scripts/land_master_bot.py
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MASTER_DB    = PROJECT_ROOT / 'data' / 'database' / 'land_master.db'


# ── .env 載入 ────────────────────────────────────────────────────

def load_dotenv():
    for p in [PROJECT_ROOT / '.env', Path.home() / '.env']:
        if p.exists():
            for line in p.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())
            break

load_dotenv()


# ── normalize 地號 ───────────────────────────────────────────────

def normalize_land_no(raw: str) -> str:
    """
    415-3    → 0415-0003
    415之3   → 0415-0003
    0415-3   → 0415-0003
    """
    s = re.sub(r'之', '-', str(raw).strip())
    s = re.sub(r'[^\d\-]', '', s)
    if not s:
        return ''
    parts = s.split('-')
    try:
        main = int(parts[0]) if parts[0] else 0
        sub  = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        return f'{main:04d}-{sub:04d}'
    except ValueError:
        return s


# ── DB 查詢 ──────────────────────────────────────────────────────

def init_log_tables(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS telegram_update_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT,
            command          TEXT,
            target_event_key TEXT,
            old_value        TEXT,
            new_value        TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS telegram_query_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id TEXT,
            query_type       TEXT,
            query_text       TEXT,
            result_count     INTEGER,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()


def init_log_table(con: sqlite3.Connection):
    init_log_tables(con)


def search_by_land_no(norm_no: str, section_hint: str = '') -> list[dict]:
    """
    用 normalized_land_no 搜尋，可加地段 hint 縮小範圍。
    回傳符合的列（dict），依 source_row DESC 排序（最新事件優先）。
    """
    con = sqlite3.connect(MASTER_DB, timeout=10)
    con.row_factory = sqlite3.Row
    if section_hint:
        rows = con.execute("""
            SELECT * FROM land_master
            WHERE normalized_land_no = ?
              AND (normalized_section LIKE ? OR section_raw LIKE ?)
            ORDER BY source_row DESC
        """, (norm_no, f'%{section_hint}%', f'%{section_hint}%')).fetchall()
    else:
        rows = con.execute("""
            SELECT * FROM land_master
            WHERE normalized_land_no = ?
            ORDER BY source_row DESC
        """, (norm_no,)).fetchall()
    result = [dict(r) for r in rows]
    con.close()
    return result


def get_latest_event(matches: list[dict]) -> dict | None:
    """同地號可能有多個事件，取 source_row 最大的（匯入序號最後 = 最新登記）。"""
    if not matches:
        return None
    return max(matches, key=lambda r: r.get('source_row') or 0)


def candidates_text(matches: list[dict]) -> str:
    lines = ['找到多筆符合，請加地段縮小範圍：\n']
    for i, r in enumerate(matches[:8], 1):
        ek = (r.get('event_key') or '')[:8]
        lines.append(
            f"{i}. {r.get('city','')} {r.get('district','')} "
            f"{r.get('section_raw','')} {r.get('land_no_raw','')}\n"
            f"   所有人：{r.get('owner_name','?')}  key:{ek}"
        )
    lines.append('\n用法：/note 地段 地號 備註\n例：/note 內興段 415-3 有意願')
    return '\n'.join(lines)


def log_query(user_id: str, query_type: str, query_text: str, result_count: int):
    con = sqlite3.connect(MASTER_DB, timeout=10)
    init_log_tables(con)
    con.execute("""
        INSERT INTO telegram_query_log
            (telegram_user_id, query_type, query_text, result_count)
        VALUES (?,?,?,?)
    """, (user_id, query_type, query_text, result_count))
    con.commit()
    con.close()


# ── 查詢格式化 ───────────────────────────────────────────────────

def _reg_date_display(raw: str) -> str:
    """113/05/28 格式直接顯示；YYYY-MM-DD 轉民國年。"""
    if not raw:
        return '—'
    raw = str(raw).strip()
    if re.match(r'^\d{3}/\d{2}/\d{2}$', raw):
        return raw
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', raw)
    if m:
        roc = int(m.group(1)) - 1911
        return f'{roc}/{m.group(2)}/{m.group(3)}'
    return raw


def format_record(r: dict) -> str:
    sold = '是' if r.get('is_sold') == 1 else '否'
    area = r.get('actual_owned_area')
    area_str = f'{area:.1f} 坪' if area else '—'
    av = r.get('announced_value')
    av_str = f'{int(av):,} 元/㎡' if av else '—'
    lines = [
        f"📍 {r.get('city','')} {r.get('district','')} {r.get('section_raw','')} {r.get('land_no_raw','')}",
        f"👤 所有人：{r.get('owner_name') or '—'}",
        f"📞 電話：{r.get('phone') or '—'}",
        f"🏠 地址：{r.get('address') or '—'}",
        f"📌 分區：{r.get('zone_type') or '—'}",
        f"📄 登記原因：{r.get('reg_reason') or '—'}",
        f"📅 登記日期：{_reg_date_display(r.get('reg_date',''))}",
        f"💰 公告現值：{av_str}",
        f"📐 持有坪數：{area_str}",
        f"📝 備註：{r.get('note') or '—'}",
        f"🏷 已售出：{sold}",
    ]
    lt = r.get('location_tag')
    if lt:
        lines.append(f"🗂 位置標記：{lt}")
    return '\n'.join(lines)


def format_candidates(rows: list[dict], hint: str = '') -> str:
    lines = [f'找到 {len(rows)} 筆，顯示前 10 筆。請加更精準條件。\n']
    if hint:
        lines[0] = f'找到 {len(rows)} 筆（{hint}），顯示前 10 筆。請加更精準條件。\n'
    for i, r in enumerate(rows[:10], 1):
        ek = (r.get('event_key') or '')[:8]
        ph = r.get('phone') or '—'
        lines.append(
            f"{i}. {r.get('section_raw','')} {r.get('land_no_raw','')}"
            f"  👤{r.get('owner_name','?')}  📞{ph}  key:{ek}"
        )
    return '\n'.join(lines)


# ── /query 核心 ──────────────────────────────────────────────────

def _looks_like_land_no(token: str) -> bool:
    return bool(re.search(r'\d', token)) and bool(re.search(r'[\d\-之]', token))


def query_dispatch(args: list[str], user_id: str) -> str:
    if not args:
        return '用法：/query 地號 | 地段 地號 | 姓名 | 電話關鍵字 | 地址關鍵字'

    raw = ' '.join(args)
    con = sqlite3.connect(MASTER_DB, timeout=10)
    con.row_factory = sqlite3.Row

    # ── 地號（含可選地段）：第一 token 無數字視為地段 hint
    section_hint = ''
    land_raw = ''
    if not re.search(r'\d', args[0]):
        # 第一 token 是純文字 → 地段 hint，第二 token 是地號
        if len(args) >= 2 and _looks_like_land_no(args[1]):
            section_hint = args[0]
            land_raw = args[1]
    elif _looks_like_land_no(args[0]):
        land_raw = args[0]

    if land_raw:
        norm_no = normalize_land_no(land_raw)
        if norm_no:
            if section_hint:
                rows = con.execute("""
                    SELECT * FROM land_master
                    WHERE normalized_land_no = ?
                      AND (normalized_section LIKE ? OR section_raw LIKE ?)
                    ORDER BY reg_date DESC, source_row DESC
                """, (norm_no, f'%{section_hint}%', f'%{section_hint}%')).fetchall()
            else:
                rows = con.execute("""
                    SELECT * FROM land_master
                    WHERE normalized_land_no = ?
                    ORDER BY reg_date DESC, source_row DESC
                """, (norm_no,)).fetchall()
            con.close()
            rows = [dict(r) for r in rows]
            log_query(user_id, 'land_no', raw, len(rows))
            if not rows:
                return f'找不到地號 {norm_no}。'
            # 同地號取每個 land_match_key 最新一筆
            seen_lmk: dict[str, dict] = {}
            for r in rows:
                lmk = r.get('land_match_key') or ''
                if lmk not in seen_lmk:
                    seen_lmk[lmk] = r
            latest_rows = list(seen_lmk.values())
            if len(latest_rows) == 1:
                return format_record(latest_rows[0])
            return format_candidates(latest_rows, f'地號 {norm_no}')

    # ── 姓名查詢（純中文 or 2-4 字無數字）
    if re.match(r'^[一-鿿]{2,6}$', raw.replace(' ', '')):
        rows = con.execute("""
            SELECT * FROM land_master
            WHERE owner_name LIKE ?
            ORDER BY reg_date DESC, source_row DESC
        """, (f'%{raw}%',)).fetchall()
        con.close()
        rows = [dict(r) for r in rows]
        log_query(user_id, 'owner_name', raw, len(rows))
        if not rows:
            return f'找不到所有人「{raw}」。'
        # 每個 land_match_key 取最新一筆
        seen_lmk: dict[str, dict] = {}
        for r in rows:
            lmk = r.get('land_match_key') or ''
            if lmk not in seen_lmk:
                seen_lmk[lmk] = r
        latest_rows = list(seen_lmk.values())
        if len(latest_rows) == 1:
            return format_record(latest_rows[0])
        return format_candidates(latest_rows, f'所有人「{raw}」')

    # ── 電話關鍵字（全數字 or 09xx 開頭）
    if re.match(r'^[\d\-]+$', raw) and len(raw) >= 4:
        rows = con.execute("""
            SELECT * FROM land_master
            WHERE phone LIKE ?
            ORDER BY reg_date DESC, source_row DESC
        """, (f'%{raw}%',)).fetchall()
        con.close()
        rows = [dict(r) for r in rows]
        log_query(user_id, 'phone', raw, len(rows))
        if not rows:
            return f'找不到電話含「{raw}」的地主。'
        seen_lmk: dict[str, dict] = {}
        for r in rows:
            lmk = r.get('land_match_key') or ''
            if lmk not in seen_lmk:
                seen_lmk[lmk] = r
        latest_rows = list(seen_lmk.values())
        if len(latest_rows) == 1:
            return format_record(latest_rows[0])
        return format_candidates(latest_rows, f'電話「{raw}」')

    # ── 地址關鍵字（fallback）
    rows = con.execute("""
        SELECT * FROM land_master
        WHERE address LIKE ?
        ORDER BY reg_date DESC, source_row DESC
    """, (f'%{raw}%',)).fetchall()
    con.close()
    rows = [dict(r) for r in rows]
    log_query(user_id, 'address', raw, len(rows))
    if not rows:
        return f'找不到地址含「{raw}」的資料。'
    seen_lmk: dict[str, dict] = {}
    for r in rows:
        lmk = r.get('land_match_key') or ''
        if lmk not in seen_lmk:
            seen_lmk[lmk] = r
    latest_rows = list(seen_lmk.values())
    if len(latest_rows) == 1:
        return format_record(latest_rows[0])
    return format_candidates(latest_rows, f'地址「{raw}」')


# ── /history 核心 ────────────────────────────────────────────────

def history_dispatch(args: list[str], user_id: str) -> str:
    if not args:
        return '用法：/history 地號\n或：/history 地段 地號'
    section_hint, land_raw, _ = parse_land_no_args(args)
    norm_no = normalize_land_no(land_raw)
    if not norm_no:
        return f'無法解析地號：{land_raw!r}'

    con = sqlite3.connect(MASTER_DB, timeout=10)
    con.row_factory = sqlite3.Row
    if section_hint:
        rows = con.execute("""
            SELECT event_key, section_raw, land_no_raw, owner_name,
                   reg_reason, reg_date, reg_seq, share_numer, share_denom,
                   actual_owned_area, is_sold, note
            FROM land_master
            WHERE normalized_land_no = ?
              AND (normalized_section LIKE ? OR section_raw LIKE ?)
            ORDER BY reg_date ASC, reg_seq ASC, source_row ASC
        """, (norm_no, f'%{section_hint}%', f'%{section_hint}%')).fetchall()
    else:
        rows = con.execute("""
            SELECT event_key, section_raw, land_no_raw, owner_name,
                   reg_reason, reg_date, reg_seq, share_numer, share_denom,
                   actual_owned_area, is_sold, note
            FROM land_master
            WHERE normalized_land_no = ?
            ORDER BY reg_date ASC, reg_seq ASC, source_row ASC
        """, (norm_no,)).fetchall()
    con.close()
    rows = [dict(r) for r in rows]
    log_query(user_id, 'history', ' '.join(args), len(rows))

    if not rows:
        return f'找不到地號 {norm_no} 的歷史事件。'

    lines = [f'📜 {norm_no} 歷史登記事件（共 {len(rows)} 筆）\n']
    for i, r in enumerate(rows, 1):
        area = r.get('actual_owned_area')
        area_str = f'{area:.1f} 坪' if area else '—'
        dn = r.get('share_denom') or 1
        nm = r.get('share_numer') or 0
        ek = (r.get('event_key') or '')[:8]
        sold_mark = ' ✅已售' if r.get('is_sold') == 1 else ''
        lines.append(
            f"{i}. [{_reg_date_display(r.get('reg_date',''))}] "
            f"{r.get('reg_reason') or '—'}  "
            f"👤{r.get('owner_name','?')}  "
            f"持分:{int(nm)}/{int(dn)}({area_str}){sold_mark}"
        )
        if r.get('note'):
            lines.append(f"   📝 {r['note'][:40]}")
    return '\n'.join(lines)


# ── /owner 核心 ─────────────────────────────────────────────────

def owner_dispatch(args: list[str], user_id: str) -> str:
    if not args:
        return '用法：/owner 姓名'
    name = ' '.join(args)

    con = sqlite3.connect(MASTER_DB, timeout=10)
    con.row_factory = sqlite3.Row

    # 每個 (owner_name, owner_key) 組合分別統計
    summary_rows = con.execute("""
        SELECT owner_name, owner_key,
               COUNT(DISTINCT land_match_key) AS parcel_count,
               SUM(actual_owned_area)         AS total_area
        FROM land_master
        WHERE owner_name LIKE ?
        GROUP BY owner_name, owner_key
        ORDER BY total_area DESC
    """, (f'%{name}%',)).fetchall()

    if not summary_rows:
        con.close()
        log_query(user_id, 'owner', name, 0)
        return f'找不到所有人「{name}」。'

    # 土地明細：每個 (owner_name, owner_key, land_match_key) 取最新事件
    detail_rows = con.execute("""
        SELECT owner_name, owner_key, land_match_key,
               section_raw, land_no_raw, city, district,
               actual_owned_area, is_sold,
               MAX(source_row) AS latest_row
        FROM land_master
        WHERE owner_name LIKE ?
        GROUP BY owner_name, owner_key, land_match_key
        ORDER BY owner_name, owner_key, actual_owned_area DESC
    """, (f'%{name}%',)).fetchall()
    con.close()

    summary_rows = [dict(r) for r in summary_rows]
    detail_rows  = [dict(r) for r in detail_rows]
    log_query(user_id, 'owner', name, len(detail_rows))

    lines = []
    for owner in summary_rows:
        ok   = owner['owner_key']
        nm_  = owner['owner_name']
        total = owner['total_area'] or 0
        lines.append(
            f"👤 {nm_}  "
            f"（共 {owner['parcel_count']} 筆土地，合計 {total:.1f} 坪）"
        )
        details = [d for d in detail_rows
                   if d['owner_name'] == nm_ and d['owner_key'] == ok]
        for d in details[:15]:
            sold_mark = ' ✅售' if d.get('is_sold') == 1 else ''
            area = d.get('actual_owned_area')
            area_str = f'{area:.1f} 坪' if area else '—'
            lines.append(
                f"  • {d.get('city','')}{d.get('district','')} "
                f"{d.get('section_raw','')} {d.get('land_no_raw','')}"
                f"  {area_str}{sold_mark}"
            )
        if len(details) > 15:
            lines.append(f"  … 還有 {len(details)-15} 筆")
        lines.append('')

    return '\n'.join(lines).rstrip()


def write_note(event_key: str, new_note: str, user_id: str) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    con = sqlite3.connect(MASTER_DB, timeout=10)
    init_log_table(con)
    row = con.execute(
        'SELECT note FROM land_master WHERE event_key=?', (event_key,)).fetchone()
    if not row:
        con.close()
        return '找不到該筆資料，未更新。'
    old_note = row[0] or ''
    separator = '\n' if old_note else ''
    appended = f"{old_note}{separator}[{now}] {new_note}"
    con.execute('BEGIN')
    con.execute('UPDATE land_master SET note=?, imported_at=datetime("now") WHERE event_key=?',
                (appended, event_key))
    con.execute("""
        INSERT INTO telegram_update_log
            (telegram_user_id, command, target_event_key, old_value, new_value)
        VALUES (?,?,?,?,?)
    """, (user_id, '/note', event_key, old_note, appended))
    con.execute('COMMIT')
    con.close()
    return f'✅ 備註已追加\n舊：{old_note or "（空）"}\n新增：{new_note}'


def write_phone(event_key: str, new_phone: str, user_id: str) -> str:
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    con = sqlite3.connect(MASTER_DB, timeout=10)
    init_log_table(con)
    row = con.execute(
        'SELECT phone FROM land_master WHERE event_key=?', (event_key,)).fetchone()
    if not row:
        con.close()
        return '找不到該筆資料，未更新。'
    old_phone = row[0] or ''
    separator = '、' if old_phone else ''
    appended = f"{old_phone}{separator}{new_phone}[{now}]"
    con.execute('BEGIN')
    con.execute('UPDATE land_master SET phone=?, imported_at=datetime("now") WHERE event_key=?',
                (appended, event_key))
    con.execute("""
        INSERT INTO telegram_update_log
            (telegram_user_id, command, target_event_key, old_value, new_value)
        VALUES (?,?,?,?,?)
    """, (user_id, '/phone', event_key, old_phone, appended))
    con.execute('COMMIT')
    con.close()
    return f'✅ 電話已追加\n舊：{old_phone or "（空）"}\n新增：{new_phone}'


def write_sold(event_key: str, user_id: str) -> str:
    con = sqlite3.connect(MASTER_DB, timeout=10)
    init_log_table(con)
    row = con.execute(
        'SELECT is_sold, owner_name, land_no_raw FROM land_master WHERE event_key=?',
        (event_key,)).fetchone()
    if not row:
        con.close()
        return '找不到該筆資料，未更新。'
    old_val = str(row[0])
    if row[0] == 1:
        con.close()
        return f'ℹ️ {row[1]} / {row[2]} 已是已售出狀態，無需重複標記。'
    con.execute('BEGIN')
    con.execute('UPDATE land_master SET is_sold=1, imported_at=datetime("now") WHERE event_key=?',
                (event_key,))
    con.execute("""
        INSERT INTO telegram_update_log
            (telegram_user_id, command, target_event_key, old_value, new_value)
        VALUES (?,?,?,?,?)
    """, (user_id, '/sold', event_key, old_val, '1'))
    con.execute('COMMIT')
    con.close()
    return f'✅ 已標記售出\n所有人：{row[1]}  地號：{row[2]}\n（資料保留，仍可作未來客戶追蹤）'


# ── 指令解析 ─────────────────────────────────────────────────────

def parse_land_no_args(args: list[str]) -> tuple[str, str, list[str]]:
    """
    解析地號 + 可選地段 hint。
    支援格式：
      [地段] 地號 [其餘...]
      地號 [其餘...]
    回傳 (section_hint, raw_land_no, rest)
    """
    if not args:
        return '', '', []
    # 第一個 token 若不含數字，視為地段 hint
    if args[0] and not re.search(r'\d', args[0]):
        section_hint = args[0]
        land_raw     = args[1] if len(args) > 1 else ''
        rest         = args[2:]
    else:
        section_hint = ''
        land_raw     = args[0]
        rest         = args[1:]
    return section_hint, land_raw, rest


def dispatch(text: str, user_id: str) -> str:
    parts = text.strip().split()
    if not parts:
        return help_text()
    cmd = parts[0].lower()

    # alias 展開：短指令 → 完整指令
    _ALIAS = {'/q': '/query', '/n': '/note', '/p': '/phone',
              '/s': '/sold',  '/h': '/history', '/o': '/owner'}
    if cmd in _ALIAS:
        cmd = _ALIAS[cmd]
        parts = [cmd] + parts[1:]

    # /whoami
    if cmd == '/whoami':
        return f'🪪 你的 Telegram user_id：{user_id}\n加入白名單請將此 ID 加到 .env TELEGRAM_ALLOWED_USERS'

    # /note 地號 備註  或  /note 地段 地號 備註
    if cmd == '/note':
        args = parts[1:]
        if len(args) < 2:
            return '用法：/note 地號 備註\n或：/note 地段 地號 備註'
        section_hint, land_raw, rest = parse_land_no_args(args)
        if not rest:
            return '請提供備註內容\n用法：/note 地號 備註'
        note_text = ' '.join(rest)
        return _handle_write(land_raw, section_hint, user_id, 'note', note_text)

    # /phone 地號 電話  或  /phone 地段 地號 電話
    if cmd == '/phone':
        args = parts[1:]
        if len(args) < 2:
            return '用法：/phone 地號 電話\n或：/phone 地段 地號 電話'
        section_hint, land_raw, rest = parse_land_no_args(args)
        if not rest:
            return '請提供電話\n用法：/phone 地號 電話'
        phone_text = ' '.join(rest)
        return _handle_write(land_raw, section_hint, user_id, 'phone', phone_text)

    # /sold 地號  或  /sold 地段 地號
    if cmd == '/sold':
        args = parts[1:]
        if not args:
            return '用法：/sold 地號\n或：/sold 地段 地號'
        section_hint, land_raw, _ = parse_land_no_args(args)
        return _handle_write(land_raw, section_hint, user_id, 'sold', '')

    # /query
    if cmd == '/query':
        return query_dispatch(parts[1:], user_id)

    # /history 地號  或  /history 地段 地號
    if cmd == '/history':
        if not parts[1:]:
            return '用法：/history 地號\n或：/history 地段 地號'
        return history_dispatch(parts[1:], user_id)

    # /owner 姓名
    if cmd == '/owner':
        if not parts[1:]:
            return '用法：/owner 姓名'
        return owner_dispatch(parts[1:], user_id)

    if cmd == '/start' or cmd == '/help':
        return help_text()

    return f'未知指令：{cmd}\n{help_text()}'


def _handle_write(land_raw: str, section_hint: str,
                  user_id: str, action: str, value: str) -> str:
    norm_no = normalize_land_no(land_raw)
    if not norm_no:
        return f'無法解析地號：{land_raw!r}'

    matches = search_by_land_no(norm_no, section_hint)
    if not matches:
        hint = f'（地段：{section_hint}）' if section_hint else ''
        return f'找不到地號 {norm_no}{hint}，請確認輸入。'

    # 唯一地號（不論幾個事件，若所有事件都是同地號 → 取最新）
    unique_lmks = {r.get('land_match_key') for r in matches}
    if len(unique_lmks) == 1:
        target = get_latest_event(matches)
        ek = target['event_key']
        if action == 'note':
            return write_note(ek, value, user_id)
        elif action == 'phone':
            return write_phone(ek, value, user_id)
        elif action == 'sold':
            return write_sold(ek, user_id)
    else:
        return candidates_text(matches)


def help_text() -> str:
    return (
        '🗂 土地情報系統指令\n\n'
        '── 查詢 ──\n'
        '/query 415-3          — 地號查詢\n'
        '/query 內興段 415-3   — 地段+地號\n'
        '/query 王先生         — 所有人查詢\n'
        '/query 0912           — 電話關鍵字\n'
        '/query 中正路         — 地址關鍵字\n'
        '/history 415-3        — 歷史登記事件\n'
        '/owner 王先生         — 地主名下所有土地\n\n'
        '── 回填 ──\n'
        '/note 地號 備註       — 追加備註\n'
        '/phone 地號 電話      — 追加電話\n'
        '/sold 地號            — 標記已售出\n\n'
        '/whoami               — 查詢自己的 user_id\n\n'
        '地號格式均可：415-3 / 415之3 / 0415-0003'
    )


# ── Bot 主體 ─────────────────────────────────────────────────────

class LandMasterBot:
    def __init__(self):
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if not self.token:
            raise RuntimeError('TELEGRAM_BOT_TOKEN 未設定')

        raw = os.environ.get('TELEGRAM_ALLOWED_USERS', '')
        self.allowed: set[str] = {u.strip() for u in raw.split(',') if u.strip()}
        if not self.allowed:
            print('⚠️  TELEGRAM_ALLOWED_USERS 未設定，所有人都可操作（不建議）')

        self.offset = 0
        self.base = f'https://api.telegram.org/bot{self.token}'

    def api(self, method: str, params: dict = None) -> dict:
        url  = f'{self.base}/{method}'
        data = json.dumps(params or {}).encode()
        req  = urllib.request.Request(url, data=data,
                                      headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=35) as r:
            return json.loads(r.read())

    def send(self, chat_id, text: str):
        for chunk in self._split(text):
            try:
                self.api('sendMessage', {'chat_id': chat_id, 'text': chunk})
            except Exception as e:
                print(f'send error: {e}')

    def _split(self, text: str, limit: int = 3800) -> list[str]:
        if len(text) <= limit:
            return [text]
        chunks, buf = [], []
        for line in text.splitlines(keepends=True):
            if sum(len(l) for l in buf) + len(line) > limit:
                chunks.append(''.join(buf))
                buf = []
            buf.append(line)
        if buf:
            chunks.append(''.join(buf))
        return chunks

    def process(self, update: dict):
        msg = update.get('message', {})
        if not msg or 'text' not in msg:
            return
        chat_id = msg['chat']['id']
        user_id = str(msg.get('from', {}).get('id', ''))
        text    = msg['text'].strip()

        # /whoami 不需白名單
        if text.lower().startswith('/whoami'):
            self.send(chat_id, f'🪪 你的 Telegram user_id：{user_id}')
            return

        # 白名單檢查
        if self.allowed and user_id not in self.allowed:
            self.send(chat_id, '未授權使用')
            print(f'[BLOCKED] user_id={user_id} text={text!r}')
            return

        reply = dispatch(text, user_id)
        self.send(chat_id, reply)

    def run(self):
        print(f'LandMasterBot 啟動，DB：{MASTER_DB}')
        print(f'白名單：{self.allowed or "（未設定）"}')
        while True:
            try:
                res = self.api('getUpdates', {
                    'offset': self.offset, 'timeout': 30,
                    'allowed_updates': ['message']
                })
                for upd in res.get('result', []):
                    self.offset = upd['update_id'] + 1
                    try:
                        self.process(upd)
                    except Exception as e:
                        print(f'process error: {e}')
                time.sleep(1)
            except KeyboardInterrupt:
                print('Bot stopped.')
                break
            except Exception as e:
                print(f'polling error: {e}')
                time.sleep(5)


if __name__ == '__main__':
    LandMasterBot().run()
