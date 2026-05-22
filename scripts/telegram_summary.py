#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
telegram_summary.py — 土地情報摘要 Telegram 推播
整合 intel_591 diff + hot + intel_rules，輸出情報摘要。

用法：
  python3 scripts/telegram_summary.py          # 產生摘要並推播
  python3 scripts/telegram_summary.py --dry    # 只印不推播
"""

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUSH_LOG     = PROJECT_ROOT / 'logs' / 'summary_push_log.json'
DB_PATH      = PROJECT_ROOT / 'db' / 'land_intel.db'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
from intel_591  import detect_diff, hot_listings
from intel_rules import run_rules


# ── 環境 ─────────────────────────────────────────────────

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


# ── 格式化 ───────────────────────────────────────────────

def _fmt_new(item: dict) -> str:
    row  = item['row']
    city = row.get('city', '')
    dist = row.get('district', '')
    sec  = row.get('section_raw', '') or ''
    ltype = row.get('land_type', '')
    area  = row.get('area_ping')
    price = row.get('total_price_wan')
    is_agent = row.get('is_agent', 1)
    url   = row.get('url', '')

    loc   = f'{city}{dist}'
    if sec and sec not in dist:
        loc += f'｜{sec}'

    parts = [f'{ltype}']
    if area:
        parts.append(f'{area:.0f}坪')
    if price:
        parts.append(f'{price:.0f}萬')
    if not is_agent:
        parts.append('地主自售')

    detail = '｜'.join(parts)
    reason = item.get('reason', '')
    lines  = [f'▪ {loc}　{detail}']
    if reason and reason != '一般新上架':
        lines.append(f'  {reason}')
    if url:
        lines.append(f'  {url}')
    return '\n'.join(lines)


def _fmt_drop(item: dict) -> str:
    row    = item['row']
    city   = row.get('city', '')
    dist   = row.get('district', '')
    ltype  = row.get('land_type', '')
    prev   = row.get('prev_price')
    curr   = row.get('total_price_wan')
    url    = row.get('url', '')
    reason = item.get('reason', '')

    loc    = f'{city}{dist}　{ltype}'
    price  = f'{prev:.0f}→{curr:.0f}萬' if prev and curr else ''
    lines  = [f'▪ {loc}']
    if price:
        lines.append(f'  {price}　{reason}')
    if url:
        lines.append(f'  {url}')
    return '\n'.join(lines)


def _fmt_hot(item: dict) -> str:
    z     = item['zone']
    city  = z.get('city', '')
    dist  = z.get('district', '')
    sec   = z.get('section_raw', '') or ''
    ltype = z.get('land_type', '')
    cnt   = z.get('listing_count', 0)
    avg   = z.get('avg_price')

    loc = f'{city}{dist}'
    if sec and sec not in dist:
        loc += f'｜{sec}'
    avg_str = f'均價{avg:.0f}萬' if avg else ''
    return f'▪ {loc}　{ltype}　{cnt}筆上架　{avg_str}'


def build_summary(diff: dict, rules: dict) -> str:
    today = date.today().strftime('%m/%d')
    lines = [f'🛰 老蕭 LAND 土地情報｜{today}', '']

    a_items = rules.get('A', [])
    b_items = rules.get('B', [])
    hot     = rules.get('hot_watchlist', [])

    if not a_items and not b_items and not hot:
        no_change = diff.get('new', []) == [] and diff.get('removed_count', 0) == 0
        if no_change or 'error' in diff:
            return f'🛰 老蕭 LAND 土地情報｜{today}\n\n今日無重大土地異動情報。'
        return f'🛰 老蕭 LAND 土地情報｜{today}\n\n今日異動不在監控範圍內，無需推播。'

    # ── A 級情報 ──────────────────────────────────────────
    if a_items:
        lines.append('【A級情報】')
        for item in a_items[:5]:   # 最多顯示 5 筆
            if 'row' in item and item['row'].get('listed_ts'):
                lines.append(_fmt_new(item))
            elif 'row' in item and item['row'].get('prev_price'):
                lines.append(_fmt_drop(item))
            else:
                lines.append(_fmt_new(item))
            lines.append('')

    # ── B 級情報摘要（不逐筆列，只統計）────────────────
    if b_items:
        # 依縣市區分組
        city_counts: dict[str, int] = {}
        for item in b_items:
            city = item.get('row', {}).get('city', '其他')
            city_counts[city] = city_counts.get(city, 0) + 1
        summary_parts = [f'{c} {n}筆' for c, n in sorted(city_counts.items(), key=lambda x: -x[1])]
        lines.append(f'【B級情報】{len(b_items)}筆一般上架（{" / ".join(summary_parts[:4])}）')
        lines.append('')

    # ── 監控熱區 ──────────────────────────────────────────
    if hot:
        lines.append('【監控熱區】')
        for item in hot[:5]:
            lines.append(_fmt_hot(item))
        lines.append('')

    # ── 下架統計 ──────────────────────────────────────────
    removed = diff.get('removed_count', 0)
    if removed:
        lines.append(f'🔻 下架 {removed} 筆')

    return '\n'.join(lines).rstrip()


# ── 去重推播 ─────────────────────────────────────────────

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


def run(db_path=None, dry=False):
    path = db_path or str(DB_PATH)

    diff      = detect_diff(db_path=path)
    hot_zones = hot_listings({'days': 7}, db_path=path, top_n=30)
    rules     = run_rules(diff, hot_zones)
    summary   = build_summary(diff, rules)

    print(summary)

    if dry:
        return

    env     = _load_env()
    token   = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[ERROR] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
        return

    today = date.today().isoformat()
    log   = _load_push_log()
    key   = f'summary:{today}'
    if log.get(key):
        print('[跳過] 今日摘要已推播過')
        return

    ok = _send_telegram(token, chat_id, summary)
    if ok:
        log[key] = today
        _save_push_log(log)
        print('[推播] 情報摘要推播成功 ✅')
    else:
        print('[ERROR] 推播失敗 ❌')


# ── 舊 snapshot 清理（保留最近 7 天）────────────────────

def cleanup_old_snapshots(db_path=None):
    import sqlite3
    from datetime import timedelta
    path    = db_path or str(DB_PATH)
    cutoff  = (date.today() - timedelta(days=7)).isoformat()
    conn    = sqlite3.connect(path)
    deleted = conn.execute(
        "DELETE FROM listing_snapshots WHERE snapshot_date < ?", (cutoff,)
    ).rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f'[清理] 已刪除 {deleted} 筆 7 天前快照')


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='土地情報摘要推播')
    parser.add_argument('--db',  default=str(DB_PATH))
    parser.add_argument('--dry', action='store_true', help='只印不推播')
    args = parser.parse_args()
    run(db_path=args.db, dry=args.dry)
    cleanup_old_snapshots(db_path=args.db)


if __name__ == '__main__':
    main()
