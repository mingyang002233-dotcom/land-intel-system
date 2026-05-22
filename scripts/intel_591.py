#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intel_591.py — 老蕭 LAND｜591 土地情報收集
抓取 591 土地出售清單，存入 SQLite，支援快照比對與異動偵測。

子命令：
  scrape   抓取並入庫（預設每次執行）
  diff     顯示自上次快照以來的異動
  hot      591 刊登熱區排行（同地段刊登數）
  push     推播新增物件與異動到 Telegram
"""

import argparse
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = PROJECT_ROOT / 'db' / 'land_intel.db'
PUSH_LOG     = PROJECT_ROOT / 'logs' / 'intel_591_push_log.json'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ── 城市 & 土地類型 設定 ─────────────────────────────────
CITIES = {
    '台北市': 1,
    '新北市': 3,
    '新竹市': 4,
    '新竹縣': 5,
    '桃園市': 6,
    '台中市': 8,
}

# 591 kind=11 為土地；layout_origin 分類
LAND_TYPES = {
    '農地': ['農地', '農業用地', '農業'],
    '建地': ['建地', '住宅用地', '商業用地', '住宅', '住', '商'],
    '工業地': ['工業用地', '工業地', '工業區', '工'],
}

# ── DB 初始化 ─────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS land_listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id      INTEGER UNIQUE,         -- 591 houseid
    city            TEXT,
    district        TEXT,                   -- section_name (行政區)
    section_raw     TEXT,                   -- address (地段原始)
    land_type       TEXT,                   -- 農地/建地/工業地/其他
    layout_origin   TEXT,                   -- 原始 layout_origin
    title           TEXT,
    area_ping       REAL,
    total_price_wan REAL,
    unit_price_wan  TEXT,
    seller_name     TEXT,
    seller_id       INTEGER,
    is_agent        INTEGER,                -- 1=仲介 0=地主自售
    tags            TEXT,                   -- JSON array
    url             TEXT,
    listed_ts       INTEGER,                -- posttime unix
    refresh_ts      INTEGER,               -- refreshtime unix
    is_down_price   INTEGER,
    scraped_at      TEXT,
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_ll_city_district ON land_listings(city, district);
CREATE INDEX IF NOT EXISTS idx_ll_land_type     ON land_listings(land_type);
CREATE INDEX IF NOT EXISTS idx_ll_listed_ts     ON land_listings(listed_ts);
CREATE INDEX IF NOT EXISTS idx_ll_listing_id    ON land_listings(listing_id);

CREATE TABLE IF NOT EXISTS listing_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date   TEXT,                   -- YYYY-MM-DD
    listing_id      INTEGER,
    city            TEXT,
    district        TEXT,
    section_raw     TEXT,
    land_type       TEXT,
    title           TEXT,
    area_ping       REAL,
    total_price_wan REAL,
    unit_price_wan  TEXT,
    is_down_price   INTEGER,
    seller_name     TEXT,
    is_agent        INTEGER,
    url             TEXT,
    UNIQUE(snapshot_date, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_ls_date ON listing_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ls_id   ON listing_snapshots(listing_id);

CREATE TABLE IF NOT EXISTS section_dictionary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    city        TEXT,
    district    TEXT,
    raw_name    TEXT,
    norm_name   TEXT,           -- 標準化後地段名
    UNIQUE(city, district, raw_name)
);
"""


def _init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _get_conn(db_path=None) -> sqlite3.Connection:
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _init_db(conn)
    return conn


# ── 591 API ───────────────────────────────────────────────

API_BASE = 'https://bff-house.591.com.tw'
PAGE_SIZE = 30


def _req_headers(regionid: int) -> dict:
    return {
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'application/json, text/plain, */*',
        'Referer': f'https://land.591.com.tw/list?type=2&region={regionid}&kind=11',
        'Origin': 'https://land.591.com.tw',
    }


def _fetch_page(regionid: int, first_row: int) -> dict:
    params = urllib.parse.urlencode({
        'type': 2,
        'regionid': regionid,
        'kind': 11,
        'firstRow': first_row,
        'totalRows': PAGE_SIZE,
    })
    url = f'{API_BASE}/v1/web/sale/list?{params}'
    req = urllib.request.Request(url, headers=_req_headers(regionid))
    with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
        return json.loads(r.read())


def _classify_land_type(layout_origin: str | None) -> str:
    if not layout_origin:
        return '其他'
    for ltype, keywords in LAND_TYPES.items():
        if any(k in layout_origin for k in keywords):
            return ltype
    return '其他'


def _parse_item(item: dict, city: str) -> dict:
    houseid     = item.get('houseid')
    area_sqm    = float(item.get('area') or 0)
    area_ping   = round(area_sqm / 3.30579, 2) if area_sqm else None
    price_wan   = float(item.get('price') or 0) or None   # 已是萬
    layout      = item.get('layout_origin') or ''
    land_type   = _classify_land_type(layout)

    # 地主自售判斷：仲介有 is_entrust=1 或 housetype=3
    is_agent = 1 if (item.get('is_entrust') or item.get('housetype') == 3) else 0

    tags_raw = item.get('conditionids') or []
    tags     = json.dumps(tags_raw, ensure_ascii=False)

    url = f'https://sale.591.com.tw/home/house/detail/2/{houseid}.html' if houseid else ''

    return {
        'listing_id':      houseid,
        'city':            city,
        'district':        item.get('section_name') or '',   # 行政區
        'section_raw':     item.get('address') or '',        # 地段
        'land_type':       land_type,
        'layout_origin':   layout,
        'title':           item.get('title') or '',
        'area_ping':       area_ping,
        'total_price_wan': price_wan,
        'unit_price_wan':  item.get('unit_price') or '',
        'seller_name':     item.get('nick_name') or '',
        'seller_id':       item.get('user_id'),
        'is_agent':        is_agent,
        'tags':            tags,
        'url':             url,
        'listed_ts':       item.get('posttime'),
        'refresh_ts':      None,  # refreshtime 是字串如"2分鐘前"
        'is_down_price':   1 if item.get('is_down_price') else 0,
        'scraped_at':      datetime.now().isoformat(timespec='seconds'),
        'raw_json':        json.dumps(item, ensure_ascii=False),
    }


def scrape_city(city: str, regionid: int, db_path=None, delay: float = 1.0) -> dict:
    conn   = _get_conn(db_path)
    added  = 0
    updated = 0
    total_fetched = 0
    first_row = 0
    server_total = None

    print(f'  [{city}] 開始抓取...')
    while True:
        try:
            resp = _fetch_page(regionid, first_row)
        except Exception as e:
            print(f'  [{city}] 抓取失敗（firstRow={first_row}）: {e}')
            break

        if resp.get('status') != 1:
            print(f'  [{city}] API status != 1: {resp.get("msg")}')
            break

        data  = resp['data']
        items = data.get('house_list', [])
        if server_total is None:
            server_total = data.get('total', 0)
            print(f'  [{city}] 伺服器總筆數: {server_total}')

        if not items:
            break

        for item in items:
            row = _parse_item(item, city)
            try:
                conn.execute("""
                    INSERT INTO land_listings
                        (listing_id,city,district,section_raw,land_type,layout_origin,
                         title,area_ping,total_price_wan,unit_price_wan,
                         seller_name,seller_id,is_agent,tags,url,
                         listed_ts,refresh_ts,is_down_price,scraped_at,last_seen_ts,raw_json)
                    VALUES
                        (:listing_id,:city,:district,:section_raw,:land_type,:layout_origin,
                         :title,:area_ping,:total_price_wan,:unit_price_wan,
                         :seller_name,:seller_id,:is_agent,:tags,:url,
                         :listed_ts,:refresh_ts,:is_down_price,:scraped_at,:scraped_at,:raw_json)
                    ON CONFLICT(listing_id) DO UPDATE SET
                        total_price_wan = excluded.total_price_wan,
                        unit_price_wan  = excluded.unit_price_wan,
                        is_down_price   = excluded.is_down_price,
                        scraped_at      = excluded.scraped_at,
                        last_seen_ts    = excluded.scraped_at,
                        raw_json        = excluded.raw_json
                """, row)
                if conn.execute('SELECT changes()').fetchone()[0]:
                    if conn.execute(
                        'SELECT COUNT(*) FROM land_listings WHERE listing_id=? AND scraped_at=?',
                        (row['listing_id'], row['scraped_at'])
                    ).fetchone()[0]:
                        added += 1
                    else:
                        updated += 1
            except Exception as e:
                print(f'  DB error {row["listing_id"]}: {e}')

        total_fetched += len(items)
        first_row     += PAGE_SIZE

        if first_row >= (server_total or 0):
            break
        time.sleep(delay)

    conn.commit()
    conn.close()
    print(f'  [{city}] 完成：新增={added} 更新={updated} 共抓={total_fetched}')
    return {'city': city, 'added': added, 'updated': updated, 'fetched': total_fetched}


def scrape_all(db_path=None, delay: float = 1.5) -> list[dict]:
    results = []
    for city, rid in CITIES.items():
        r = scrape_city(city, rid, db_path=db_path, delay=delay)
        results.append(r)
        time.sleep(delay)
    return results


# ── 快照 ────────────────────────────────────────────────

def take_snapshot(db_path=None):
    conn  = _get_conn(db_path)
    today = date.today().isoformat()
    rows  = conn.execute("""
        SELECT listing_id,city,district,section_raw,land_type,title,
               area_ping,total_price_wan,unit_price_wan,is_down_price,
               seller_name,is_agent,url
        FROM land_listings
    """).fetchall()
    inserted = 0
    for r in rows:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO listing_snapshots
                    (snapshot_date,listing_id,city,district,section_raw,land_type,
                     title,area_ping,total_price_wan,unit_price_wan,is_down_price,
                     seller_name,is_agent,url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (today, r['listing_id'], r['city'], r['district'], r['section_raw'],
                  r['land_type'], r['title'], r['area_ping'], r['total_price_wan'],
                  r['unit_price_wan'], r['is_down_price'], r['seller_name'],
                  r['is_agent'], r['url']))
            inserted += conn.execute('SELECT changes()').fetchone()[0]
        except Exception as e:
            print(f'snapshot error: {e}')
    conn.commit()
    conn.close()
    print(f'快照完成：{today} 共 {inserted} 筆新增至快照')


# ── 異動偵測 ─────────────────────────────────────────────

def detect_diff(db_path=None) -> dict:
    conn = _get_conn(db_path)
    today = date.today().isoformat()

    # 找前一天快照日期
    prev = conn.execute(
        "SELECT snapshot_date FROM listing_snapshots WHERE snapshot_date < ? "
        "ORDER BY snapshot_date DESC LIMIT 1", (today,)
    ).fetchone()

    if not prev:
        conn.close()
        return {'error': '無前次快照可比較'}

    prev_date = prev['snapshot_date']

    today_ids = {r['listing_id'] for r in conn.execute(
        "SELECT listing_id FROM listing_snapshots WHERE snapshot_date=?", (today,)
    )}
    prev_ids = {r['listing_id'] for r in conn.execute(
        "SELECT listing_id FROM listing_snapshots WHERE snapshot_date=?", (prev_date,)
    )}

    new_ids     = today_ids - prev_ids
    removed_ids = prev_ids - today_ids

    # 降價
    down_price = conn.execute("""
        SELECT t.listing_id, t.city, t.district, t.section_raw, t.land_type,
               t.title, t.total_price_wan, p.total_price_wan AS prev_price, t.url
        FROM listing_snapshots t
        JOIN listing_snapshots p ON t.listing_id = p.listing_id
        WHERE t.snapshot_date=? AND p.snapshot_date=?
          AND t.total_price_wan IS NOT NULL AND p.total_price_wan IS NOT NULL
          AND t.total_price_wan < p.total_price_wan
    """, (today, prev_date)).fetchall()

    new_items = []
    if new_ids:
        placeholders = ','.join('?' * len(new_ids))
        new_items = conn.execute(
            f"SELECT * FROM listing_snapshots WHERE snapshot_date=? AND listing_id IN ({placeholders})",
            (today, *new_ids)
        ).fetchall()

    conn.close()
    return {
        'compare': (prev_date, today),
        'new': [dict(r) for r in new_items],
        'removed_count': len(removed_ids),
        'down_price': [dict(r) for r in down_price],
    }


def format_diff(diff: dict) -> str:
    if 'error' in diff:
        return f'⚠️ {diff["error"]}'
    prev, today = diff['compare']
    lines = [f'📊 591 刊登異動 {prev} → {today}', '']

    new_items = diff['new']
    if new_items:
        lines.append(f'🆕 新增刊登 {len(new_items)} 筆：')
        for r in new_items[:10]:
            price = f'{r["total_price_wan"]:.0f}萬' if r.get('total_price_wan') else 'N/A'
            area  = f'{r["area_ping"]:.0f}坪' if r.get('area_ping') else 'N/A'
            seller = '地主自售' if not r.get('is_agent') else '仲介'
            lines.append(f'  • {r["city"]}{r["district"]}｜{r["section_raw"]}｜{r["land_type"]}｜{area}｜{price}｜{seller}')
            lines.append(f'    {r["url"]}')
        if len(new_items) > 10:
            lines.append(f'  ...（另有 {len(new_items)-10} 筆）')
        lines.append('')

    if diff['removed_count']:
        lines.append(f'🔻 下架 {diff["removed_count"]} 筆')
        lines.append('')

    down = diff['down_price']
    if down:
        lines.append(f'💸 降價 {len(down)} 筆：')
        for r in down[:5]:
            lines.append(f'  • {r["city"]}{r["district"]} {r["title"]} {r["prev_price"]:.0f}→{r["total_price_wan"]:.0f}萬')
        lines.append('')

    return '\n'.join(lines).rstrip()


# ── 熱區排行（591 刊登量）────────────────────────────────

def hot_listings(filters: dict, db_path=None, top_n: int = 20) -> list[dict]:
    conn   = _get_conn(db_path)
    wheres = ['1=1']
    values = []

    if filters.get('city'):
        wheres.append('city=?'); values.append(filters['city'])
    if filters.get('district'):
        wheres.append('district=?'); values.append(filters['district'])
    if filters.get('land_type'):
        wheres.append('land_type=?'); values.append(filters['land_type'])

    # 只看最近 N 天刊登
    days = filters.get('days', 90)
    cutoff_ts = int(datetime.now().timestamp()) - days * 86400
    wheres.append('listed_ts >= ?'); values.append(cutoff_ts)

    where = ' AND '.join(wheres)
    sort  = filters.get('sort', 'count')
    order = {
        'count': 'listing_count DESC',
        'total': 'avg_price DESC, listing_count DESC',
    }.get(sort, 'listing_count DESC')

    sql = f"""
        SELECT city, district, section_raw, land_type,
               COUNT(*) AS listing_count,
               ROUND(AVG(total_price_wan), 0) AS avg_price,
               ROUND(MIN(total_price_wan), 0) AS min_price,
               ROUND(MAX(total_price_wan), 0) AS max_price,
               SUM(CASE WHEN is_agent=0 THEN 1 ELSE 0 END) AS owner_count
        FROM land_listings
        WHERE {where}
        GROUP BY city, district, section_raw, land_type
        ORDER BY {order}
        LIMIT {top_n}
    """
    rows = [dict(r) for r in conn.execute(sql, values).fetchall()]
    conn.close()
    return rows


def format_hot_listings(rows: list[dict], filters: dict) -> str:
    days = filters.get('days', 90)
    if not rows:
        return f'🔍 近{days}天 591 刊登查無資料。'

    medals = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
    title_parts = [v for k in ('city','district','land_type') if (v := filters.get(k))]
    scope = '｜'.join(title_parts) if title_parts else '全台'
    lines = [f'🏷 {scope}｜近{days}天 591 刊登熱區（依筆數）', '']

    for i, r in enumerate(rows):
        medal = medals[i] if i < len(medals) else f'{i+1}.'
        loc   = f'{r["city"]}{r["district"]}'
        sec   = r.get('section_raw') or ''
        ltype = r.get('land_type') or ''
        cnt   = r['listing_count']
        owner = r.get('owner_count') or 0
        avg_p = r.get('avg_price')
        min_p = r.get('min_price')
        max_p = r.get('max_price')

        avg_str   = f'{avg_p:.0f}萬' if avg_p else 'N/A'
        range_str = f'（{min_p:.0f}～{max_p:.0f}）' if min_p and max_p and min_p != max_p else ''
        owner_str = f'地主自售{owner}筆' if owner else ''

        lines.append(f'{medal} {loc}｜{sec}｜{ltype}')
        lines.append(f'📋 刊登{cnt}筆　均價{avg_str}{range_str}　{owner_str}')
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
    for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    data = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
    req  = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=data,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            return json.loads(r.read()).get('ok', False)
    except Exception as e:
        print(f'[ERROR] Telegram 發送失敗：{e}')
        return False


def _load_push_log() -> dict:
    if not PUSH_LOG.exists():
        return {}
    try:
        return json.loads(PUSH_LOG.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_push_log(log: dict):
    PUSH_LOG.parent.mkdir(parents=True, exist_ok=True)
    PUSH_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding='utf-8')


def push_diff(db_path=None):
    env     = _load_env()
    token   = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[ERROR] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
        return

    diff = detect_diff(db_path=db_path)
    msg  = format_diff(diff)

    today = date.today().isoformat()
    log   = _load_push_log()
    key   = f'diff:{today}'
    if log.get(key):
        print('[跳過] 今日異動已推播過')
        return

    ok = _send_telegram(token, chat_id, msg)
    if ok:
        log[key] = today
        _save_push_log(log)
        print('[推播] 591 異動推播成功')
    else:
        print('[ERROR] 推播失敗')


# ── CLI ──────────────────────────────────────────────────

def _print_area_listings(area_kw: str, filters: dict, db_path=None,
                         limit: int = 10, with_links: bool = False):
    """按關鍵字查詢某區域的刊登，輸出含連結的詳細清單。"""
    conn = _get_conn(db_path)
    conn.row_factory = sqlite3.Row

    days  = filters.get('days', 90)
    ltype = filters.get('land_type')
    from datetime import datetime, timedelta
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp())

    sql  = """
        SELECT * FROM land_listings
        WHERE listed_ts >= ?
          AND (district LIKE ? OR section_raw LIKE ?)
    """
    params: list = [cutoff, f'%{area_kw}%', f'%{area_kw}%']
    if ltype:
        sql += ' AND land_type = ?'
        params.append(ltype)
    sql += ' ORDER BY is_agent ASC, listed_ts DESC LIMIT ?'
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    kw_str = area_kw + (f'｜{ltype}' if ltype else '')
    print(f'\n🔍 {kw_str}｜近{days}天 591 刊登（{len(rows)} 筆）\n')

    if not rows:
        print('  查無資料')
        return

    for i, r in enumerate(rows, 1):
        city   = r['city'] or ''
        dist   = r['district'] or ''
        sec    = r['section_raw'] or ''
        lt     = r['land_type'] or ''
        area   = r['area_ping'] or 0
        price  = r['total_price_wan'] or 0
        unit   = r['unit_price_wan'] or ''
        title  = r['title'] or ''
        url    = r['url'] or ''
        ag     = '地主自售' if r['is_agent'] == 0 else '仲介'
        down   = ' ⬇降價' if r['is_down_price'] == 1 else ''
        loc    = f'{city}{dist}' + (f'｜{sec}' if sec and sec not in dist else '')
        ts_str = ''
        if r['listed_ts']:
            from datetime import datetime
            ts_str = datetime.fromtimestamp(r['listed_ts']).strftime('%m/%d')

        print(f'{i:2}. {loc}｜{lt}  [{ag}]{down}  {ts_str}')
        print(f'    {area:.0f}坪｜{int(price)}萬｜{unit}')
        if title:
            print(f'    標題：{title}')
        if with_links and url:
            print(f'    {url}')
        print()


def main():
    parser = argparse.ArgumentParser(description='591 土地情報收集')
    parser.add_argument('--db', default=str(DB_PATH))
    sub = parser.add_subparsers(dest='cmd')

    sub.add_parser('scrape',   help='抓取 591 刊登資料')
    sub.add_parser('snapshot', help='對今日刊登建立快照')
    sub.add_parser('diff',     help='與前次快照比較異動')
    sub.add_parser('push',     help='推播今日異動至 Telegram')

    ph = sub.add_parser('hot', help='591 刊登熱區排行')
    ph.add_argument('--city')
    ph.add_argument('--district')
    ph.add_argument('--area',      help='地段/行政區關鍵字，例如：大竹、青埔、航空城')
    ph.add_argument('--type',      dest='land_type',
                    choices=['農地', '建地', '工業地', '其他'],
                    help='土地類型篩選')
    ph.add_argument('--days',      type=int, default=90)
    ph.add_argument('--sort',      choices=['count', 'total'], default='count')
    ph.add_argument('--top',       type=int, default=20)
    ph.add_argument('--limit',     type=int, default=10, help='每個熱區顯示幾筆案件')
    ph.add_argument('--with-links', action='store_true', dest='with_links',
                    help='顯示每筆 591 連結')

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    if args.cmd == 'scrape':
        results = scrape_all(db_path=args.db)
        total_added = sum(r['added'] for r in results)
        total_updated = sum(r['updated'] for r in results)
        print(f'\n抓取完成：新增 {total_added} 筆，更新 {total_updated} 筆')

    elif args.cmd == 'snapshot':
        take_snapshot(db_path=args.db)

    elif args.cmd == 'diff':
        diff = detect_diff(db_path=args.db)
        print(format_diff(diff))

    elif args.cmd == 'push':
        push_diff(db_path=args.db)

    elif args.cmd == 'hot':
        filters = {
            'city':      getattr(args, 'city', None),
            'district':  getattr(args, 'district', None),
            'land_type': getattr(args, 'land_type', None),
            'days':      args.days,
            'sort':      args.sort,
        }
        area_kw    = getattr(args, 'area', None)
        with_links = getattr(args, 'with_links', False)
        limit      = getattr(args, 'limit', 10)

        if area_kw:
            # 直接查 DB，以關鍵字篩選 district 或 section_raw
            _print_area_listings(area_kw, filters, db_path=args.db,
                                 limit=limit, with_links=with_links)
        else:
            rows = hot_listings(filters, db_path=args.db, top_n=args.top)
            print(format_hot_listings(rows, filters))


if __name__ == '__main__':
    main()
