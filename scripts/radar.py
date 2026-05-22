#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radar.py — 近30天土地情報雷達
  hot      熱區排行
  anomaly  異常成交雷達
"""

import argparse
import json
import os
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / 'db' / 'land_intel.db'
PUSH_LOG     = PROJECT_ROOT / 'logs' / 'radar_push_log.json'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ── 異常門檻 ──────────────────────────────────────────────
S_THRESH = 20
A_THRESH = 10
B_THRESH = 5
# C級：單筆總價 or 單價門檻（萬/坪），低於此筆數的地段才納入
C_UNIT_THRESH = 200   # 萬/坪
C_TOTAL_THRESH = 5000 # 萬


def _cutoff(days: int = 30) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _parse_section(section_name: str | None):
    """拆分地段與小段，例如 '榮星段五小段' → ('榮星段', '五小段')"""
    if not section_name:
        return '', ''
    m = re.match(r'^(.+段)(.+小段)$', section_name)
    if m:
        return m.group(1), m.group(2)
    return section_name, ''


def _normalize_zone(zone: str | None) -> str:
    """將冗長地目縮短顯示，例如 '都市：其他:道路用地' → '其他(道路)'"""
    if not zone:
        return ''
    if zone.startswith('都市：其他:'):
        inner = zone.replace('都市：其他:', '')
        return f'其他({inner})'
    return zone


def _base_sql(filters: dict) -> tuple[str, list]:
    """
    依 filters 產生 WHERE 條件（city/district/section_name/land_use_zone/days）。
    回傳 (where_clause, values)。
    """
    days = filters.get('days', 30)
    wheres = ['trade_date >= ?']
    values = [_cutoff(days)]

    if filters.get('city'):
        wheres.append('city = ?')
        values.append(filters['city'])
    if filters.get('district'):
        wheres.append('district = ?')
        values.append(filters['district'])
    if filters.get('section_name'):
        # 支援精確或前綴比對
        wheres.append('section_name LIKE ?')
        values.append(f'{filters["section_name"]}%')
    if filters.get('land_use_zone'):
        wheres.append('land_use_zone LIKE ?')
        values.append(f'%{filters["land_use_zone"]}%')

    return ' AND '.join(wheres), values


# ── 熱區排行 ──────────────────────────────────────────────

def hot_zones(filters: dict, db_path=None, top_n: int = 20) -> list[dict]:
    path = db_path or str(DB_PATH)
    where, values = _base_sql(filters)
    sort = filters.get('sort', 'count')  # count | total | unit
    order = {
        'count': 'txn_count DESC, total_wan DESC, avg_unit DESC',
        'total': 'total_wan DESC, txn_count DESC, avg_unit DESC',
        'unit':  'avg_unit DESC, txn_count DESC, total_wan DESC',
    }.get(sort, 'txn_count DESC, total_wan DESC')

    sql = f"""
        SELECT
            city, district, section_name, land_use_zone,
            COUNT(*)                            AS txn_count,
            ROUND(AVG(unit_price_per_ping_wan), 1) AS avg_unit,
            ROUND(MAX(unit_price_per_ping_wan), 1) AS max_unit,
            ROUND(MIN(unit_price_per_ping_wan), 1) AS min_unit,
            ROUND(SUM(total_price_wan), 0)      AS total_wan,
            ROUND(MAX(total_price_wan), 0)      AS max_total
        FROM land_transactions
        WHERE {where}
        GROUP BY city, district, section_name, land_use_zone
        ORDER BY {order}
        LIMIT {top_n}
    """
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, values).fetchall()]
    return rows


def format_hot_zones(rows: list[dict], filters: dict) -> str:
    days = filters.get('days', 30)
    if not rows:
        return f'🔍 近{days}天熱區排行\n\n查無資料。'

    title_parts = []
    for k in ('city', 'district', 'section_name', 'land_use_zone'):
        if filters.get(k):
            title_parts.append(filters[k])
    scope = '｜'.join(title_parts) if title_parts else '全台'
    sort_label = {'count': '成交筆數', 'total': '總金額', 'unit': '平均單價'}.get(filters.get('sort', 'count'), '成交筆數')

    lines = [f'🔥 {scope}｜近{days}天熱區排行（依{sort_label}）', '']
    medals = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣',
              '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

    for i, r in enumerate(rows):
        medal = medals[i] if i < len(medals) else f'{i+1}.'
        section, sub = _parse_section(r.get('section_name') or '')
        zone = _normalize_zone(r.get('land_use_zone'))

        loc = r.get('district') or r.get('city') or ''
        if section:
            loc = f'{loc}｜{section}'
        if sub:
            loc += sub
        if zone:
            loc += f'｜{zone}'

        txn   = r.get('txn_count') or 0
        avg_u = r.get('avg_unit')
        max_u = r.get('max_unit')
        min_u = r.get('min_unit')
        total = r.get('total_wan')
        max_t = r.get('max_total')

        avg_str   = f'{avg_u:.1f}萬/坪' if avg_u else 'N/A'
        range_str = ''
        if max_u and min_u and max_u != min_u:
            range_str = f'（{min_u:.0f}～{max_u:.0f}）'
        total_str = f'{total:.0f}萬' if total else 'N/A'
        max_t_str = f'{max_t:.0f}萬' if max_t else 'N/A'

        lines.append(f'{medal} {loc}')
        lines.append(f'📊 {txn}筆｜均價{avg_str}{range_str}')
        lines.append(f'💰 總額{total_str}｜最高單筆{max_t_str}')
        lines.append('')

    return '\n'.join(lines).rstrip()


# ── 異常成交雷達 ──────────────────────────────────────────

def _grade_and_reason(txn_count, avg_unit, max_total) -> tuple[str, list[str]]:
    reasons = []
    grade = ''

    if txn_count >= S_THRESH:
        grade = 'S'
        reasons.append(f'近30天成交{txn_count}筆以上，疑似熱區爆量')
    elif txn_count >= A_THRESH:
        grade = 'A'
        reasons.append(f'近30天成交{txn_count}筆以上，需追蹤地主與建商布局')
    elif txn_count >= B_THRESH:
        grade = 'B'
        reasons.append(f'近30天成交{txn_count}筆，異常聚集，建議持續觀察')

    # C 級補充（不覆蓋已有等級，但可累加原因）
    if max_total and max_total >= C_TOTAL_THRESH:
        if not grade:
            grade = 'C'
        reasons.append(f'單筆總價{max_total:.0f}萬，建議列入觀察')
    if avg_unit and avg_unit >= C_UNIT_THRESH:
        if not grade:
            grade = 'C'
        reasons.append(f'均價{avg_unit:.1f}萬/坪，可能為特殊成交或指標案件')

    return grade, reasons


def anomaly_radar(filters: dict, db_path=None) -> list[dict]:
    path = db_path or str(DB_PATH)
    f = dict(filters)
    f.setdefault('days', 30)
    where, values = _base_sql(f)

    sql = f"""
        SELECT
            city, district, section_name, land_use_zone,
            COUNT(*)                               AS txn_count,
            ROUND(AVG(unit_price_per_ping_wan), 1) AS avg_unit,
            ROUND(MAX(unit_price_per_ping_wan), 1) AS max_unit,
            ROUND(MAX(total_price_wan), 0)         AS max_total,
            ROUND(SUM(total_price_wan), 0)         AS total_wan
        FROM land_transactions
        WHERE {where}
        GROUP BY city, district, section_name, land_use_zone
        HAVING txn_count >= ? OR max_total >= ? OR avg_unit >= ?
        ORDER BY txn_count DESC, total_wan DESC
    """
    values += [B_THRESH, C_TOTAL_THRESH, C_UNIT_THRESH]

    results = []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute(sql, values).fetchall():
            row = dict(r)
            grade, reasons = _grade_and_reason(
                row.get('txn_count') or 0,
                row.get('avg_unit'),
                row.get('max_total'),
            )
            if grade:
                row['grade'] = grade
                row['reasons'] = reasons
                results.append(row)

    # 依等級排序 S > A > B > C
    order = {'S': 0, 'A': 1, 'B': 2, 'C': 3}
    results.sort(key=lambda x: (order.get(x['grade'], 9), -(x.get('txn_count') or 0)))
    return results


_GRADE_ICON = {'S': '🚨', 'A': '⚠️', 'B': '📌', 'C': '🔎'}


def format_anomaly_radar(rows: list[dict], filters: dict) -> str:
    days = filters.get('days', 30)
    if not rows:
        return f'✅ 近{days}天無異常成交訊號。'

    title_parts = []
    for k in ('city', 'district', 'section_name'):
        if filters.get(k):
            title_parts.append(filters[k])
    scope = '｜'.join(title_parts) if title_parts else '全台'

    lines = [f'🛰 {scope}｜近{days}天異常成交雷達', '']
    for r in rows:
        grade = r.get('grade', '?')
        icon = _GRADE_ICON.get(grade, '🔎')
        section, sub = _parse_section(r.get('section_name') or '')
        zone = _normalize_zone(r.get('land_use_zone'))

        loc = r.get('district') or r.get('city') or ''
        if section:
            loc += f'｜{section}'
        if sub:
            loc += sub
        if zone:
            loc += f'｜{zone}'

        txn   = r.get('txn_count') or 0
        avg_u = r.get('avg_unit')
        max_u = r.get('max_unit')
        max_t = r.get('max_total')
        total = r.get('total_wan')

        avg_str   = f'{avg_u:.1f}萬/坪' if avg_u else 'N/A'
        max_u_str = f'{max_u:.1f}萬/坪' if max_u else 'N/A'
        max_t_str = f'{max_t:.0f}萬'   if max_t else 'N/A'
        total_str = f'{total:.0f}萬'   if total else 'N/A'

        lines.append(f'{icon} [{grade}級] {loc}')
        lines.append(f'📊 {txn}筆｜均價{avg_str}｜最高單價{max_u_str}')
        lines.append(f'💰 最高單筆{max_t_str}｜總額{total_str}')
        for reason in r.get('reasons', []):
            lines.append(f'  ▸ {reason}')
        lines.append('')

    return '\n'.join(lines).rstrip()


# ── Telegram 推播 ────────────────────────────────────────

def _load_env() -> dict:
    env = {}
    for path in [PROJECT_ROOT / '.env', PROJECT_ROOT.parent / '.env']:
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # 環境變數優先
    for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _load_push_log() -> dict:
    """回傳 {push_key: date_str}，今天已推過的 key 集合。"""
    if not PUSH_LOG.exists():
        return {}
    try:
        return json.loads(PUSH_LOG.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_push_log(log: dict):
    PUSH_LOG.parent.mkdir(parents=True, exist_ok=True)
    PUSH_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')


def _push_key(row: dict) -> str:
    """同地段同地目同一天只推一次的去重 key。"""
    return f'{row.get("city")}|{row.get("district")}|{row.get("section_name")}|{row.get("land_use_zone")}'


def _format_push_message(row: dict) -> str:
    grade = row.get('grade', '?')
    section, sub = _parse_section(row.get('section_name') or '')
    zone = _normalize_zone(row.get('land_use_zone'))

    loc_parts = [x for x in [row.get('city'), row.get('district')] if x]
    loc = ''.join(loc_parts)

    section_disp = section
    if sub:
        section_disp += sub

    avg_u = row.get('avg_unit')
    max_t = row.get('max_total')
    txn   = row.get('txn_count') or 0

    avg_str = f'{avg_u:.1f}萬/坪' if avg_u else 'N/A'
    max_str = f'{max_t:.0f}萬'   if max_t else 'N/A'
    reasons = '\n'.join(f'  ▸ {r}' for r in row.get('reasons', []))

    now = date.today().isoformat()
    return (
        f'🛰 老蕭 LAND 戰情速報\n\n'
        f'情報等級：{grade}級\n\n'
        f'地段：{loc}｜{section_disp}\n'
        f'地目：{zone or "—"}\n\n'
        f'近30天成交：{txn}筆\n'
        f'平均單價：{avg_str}\n'
        f'最高總價：{max_str}\n\n'
        f'異常原因：\n{reasons}\n\n'
        f'時間：{now}'
    )


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
    req  = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=data
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            return json.loads(r.read()).get('ok', False)
    except Exception as e:
        print(f'[ERROR] Telegram 發送失敗：{e}')
        return False


def test_push_anomaly(rows: list[dict]):
    """取第一筆異常（任何等級）發送測試訊息，不寫去重紀錄。"""
    env = _load_env()
    token   = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[ERROR] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
        return

    if not rows:
        print('[TEST] 無任何異常資料可測試。')
        return

    row = rows[0]
    section, sub = _parse_section(row.get('section_name') or '')
    zone  = _normalize_zone(row.get('land_use_zone'))
    loc   = ''.join(x for x in [row.get('city'), row.get('district')] if x)
    sec   = section + sub if sub else section
    avg_u = row.get('avg_unit')
    max_t = row.get('max_total')
    txn   = row.get('txn_count') or 0

    avg_str = f'{avg_u:.1f}萬/坪' if avg_u else 'N/A'
    max_str = f'{max_t:.0f}萬'   if max_t else 'N/A'
    reasons = '\n'.join(f'  ▸ {r}' for r in row.get('reasons', []))

    msg = (
        f'🧪【測試】老蕭 LAND 戰情速報\n\n'
        f'此為 Telegram 推播測試，不代表正式異常。\n\n'
        f'地段：{loc}｜{sec}\n'
        f'地目：{zone or "—"}\n'
        f'近30天成交：{txn}筆\n'
        f'平均單價：{avg_str}\n'
        f'最高總價：{max_str}\n'
        f'異常原因：\n{reasons}'
    )

    ok = _send_telegram(token, chat_id, msg)
    print('[TEST] 推播成功 ✅' if ok else '[TEST] 推播失敗 ❌')


def push_anomaly(rows: list[dict]):
    """推播 S/A 級異常，同地段一天只推一次。"""
    env = _load_env()
    token   = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[ERROR] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
        return

    targets = [r for r in rows if r.get('grade') in ('S', 'A')]
    if not targets:
        print('✅ 近30天無 S/A 級異常，不推播。')
        return

    log    = _load_push_log()
    today  = date.today().isoformat()
    pushed = 0

    for row in targets:
        key = _push_key(row)
        if log.get(key) == today:
            print(f'[跳過] 今日已推播：{key}')
            continue
        msg = _format_push_message(row)
        ok  = _send_telegram(token, chat_id, msg)
        if ok:
            log[key] = today
            pushed  += 1
            print(f'[推播] {row.get("grade")}級 {key}')
        else:
            print(f'[ERROR] 推播失敗：{key}')

    _save_push_log(log)
    print(f'\n完成：推播 {pushed} 筆 / 共 {len(targets)} 筆 S/A 級異常。')


# ── CLI ──────────────────────────────────────────────────

def _build_filters(args) -> dict:
    f = {'days': args.days}
    if getattr(args, 'city',         None): f['city']         = args.city
    if getattr(args, 'district',     None): f['district']     = args.district
    if getattr(args, 'section_name', None): f['section_name'] = args.section_name
    if getattr(args, 'zone',         None): f['land_use_zone'] = args.zone
    if getattr(args, 'sort',         None): f['sort']         = args.sort
    return f


def main():
    parser = argparse.ArgumentParser(description='近30天土地情報雷達')
    parser.add_argument('--db', default=str(DB_PATH))
    sub = parser.add_subparsers(dest='cmd')

    def _add_common(p):
        p.add_argument('--city',         help='縣市，例如 桃園市')
        p.add_argument('--district',     help='行政區，例如 大園區')
        p.add_argument('--section-name', dest='section_name', help='地段，例如 菓林段')
        p.add_argument('--zone',         help='地目，例如 住')
        p.add_argument('--days',         type=int, default=30, help='天數（預設30）')

    ph = sub.add_parser('hot',     help='熱區排行')
    _add_common(ph)
    ph.add_argument('--sort', choices=['count', 'total', 'unit'], default='count')
    ph.add_argument('--top',  type=int, default=20)

    pa = sub.add_parser('anomaly', help='異常成交雷達')
    _add_common(pa)
    pa.add_argument('--push',      action='store_true', help='推播 S/A 級異常至 Telegram')
    pa.add_argument('--test-push', action='store_true', dest='test_push', help='發送測試推播（取第一筆，不寫去重）')

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    filters = _build_filters(args)

    if args.cmd == 'hot':
        rows = hot_zones(filters, db_path=args.db, top_n=args.top)
        print(format_hot_zones(rows, filters))
    elif args.cmd == 'anomaly':
        rows = anomaly_radar(filters, db_path=args.db)
        print(format_anomaly_radar(rows, filters))
        if getattr(args, 'test_push', False):
            print()
            test_push_anomaly(rows)
        elif getattr(args, 'push', False):
            print()
            push_anomaly(rows)


if __name__ == '__main__':
    main()
