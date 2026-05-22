#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
keyword_trends.py — 土地/房地產熱門搜尋趨勢週報

每週抓取 Google Trends（台灣）土地相關關鍵字熱度，
比較近 7 天 vs 前 7 天判斷上升/下降趨勢。

用法：
  python3 scripts/keyword_trends.py          # 輸出本週趨勢報告
  python3 scripts/keyword_trends.py --dry    # 只印不存檔
  python3 scripts/keyword_trends.py --push   # 存檔並推 Telegram

輸出：
  data/keyword_trends_YYYY-MM-DD.json
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

try:
    from pytrends.request import TrendReq
    import pandas as pd
except ImportError:
    print('請先安裝：pip3 install pytrends pandas')
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
ENV_PATH = PROJECT_ROOT / '.env'

# ── 監控關鍵字清單（土地/房地產/重大建設）────────────────────────────
# 分批查詢，每批最多 5 個（pytrends 限制）
KEYWORD_BATCHES = [
    ['航空城', '青埔', 'A7', '桃園捷運', '捷運綠線'],
    ['台積電楠梓', '台積電', '科學園區', '竹科擴廠', '南科'],
    ['區段徵收', '市地重劃', '農地變建地', '重劃區', '自辦重劃'],
    ['土地標售', '國有地', '地上權', '容積移轉', '都市更新'],
    ['土地買賣', '建地', '農地', '工業地', '土地增值稅'],
    ['社宅', '社會住宅', '合宜住宅', '租金補貼', '囤房稅'],
    ['新竹重劃', '台中水湳', '台南鐵路地下化', '高雄亞灣', '林口重劃'],
]
# ─────────────────────────────────────────────────────────────────────

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


def _build_pytrends() -> TrendReq:
    return TrendReq(
        hl='zh-TW',
        tz=480,
        timeout=(15, 60),
        retries=2,
        backoff_factor=0.5,
    )


def _fetch_batch(pt: TrendReq, keywords: list[str], today: date) -> dict[str, dict]:
    """
    抓取一批關鍵字近 30 天的每日搜尋熱度，
    回傳 {keyword: {'recent_avg': float, 'prev_avg': float, 'trend': str, 'score': int}}
    """
    timeframe = f'{(today - timedelta(days=29)).isoformat()} {today.isoformat()}'
    try:
        pt.build_payload(keywords, timeframe=timeframe, geo='TW')
        df = pt.interest_over_time()
    except Exception as e:
        print(f'  [WARN] 批次查詢失敗 {keywords}: {e}')
        return {}

    if df is None or df.empty:
        return {}

    results = {}
    cutoff = today - timedelta(days=7)
    prev_cutoff = today - timedelta(days=14)

    for kw in keywords:
        if kw not in df.columns:
            continue
        series = df[kw]
        recent = series[series.index.date > cutoff]
        prev = series[(series.index.date > prev_cutoff) & (series.index.date <= cutoff)]

        recent_avg = float(recent.mean()) if not recent.empty else 0.0
        prev_avg = float(prev.mean()) if not prev.empty else 0.0

        if prev_avg > 0:
            change_pct = (recent_avg - prev_avg) / prev_avg * 100
        else:
            change_pct = 0.0

        if change_pct >= 20:
            trend = '↑↑'
        elif change_pct >= 5:
            trend = '↑'
        elif change_pct <= -20:
            trend = '↓↓'
        elif change_pct <= -5:
            trend = '↓'
        else:
            trend = '→'

        results[kw] = {
            'recent_avg': round(recent_avg, 1),
            'prev_avg': round(prev_avg, 1),
            'change_pct': round(change_pct, 1),
            'trend': trend,
            'score': int(round(recent_avg)),
        }
    return results


def fetch_trends(today: date) -> list[dict]:
    """抓取所有批次，依近 7 天平均熱度排序，回傳清單。"""
    pt = _build_pytrends()
    all_results = {}

    for i, batch in enumerate(KEYWORD_BATCHES):
        if i > 0:
            time.sleep(3)  # 避免 Google 限流
        print(f'  查詢批次 {i+1}/{len(KEYWORD_BATCHES)}: {batch}')
        batch_results = _fetch_batch(pt, batch, today)
        all_results.update(batch_results)

    # 依近 7 天平均熱度排序
    ranked = sorted(all_results.items(), key=lambda x: x[1]['recent_avg'], reverse=True)
    return [{'keyword': kw, **info} for kw, info in ranked]


def format_report(rows: list[dict], today: date) -> str:
    """產生 Telegram 可直接推播的文字報告。"""
    rising = [r for r in rows if '↑' in r['trend']]
    hot = rows[:15]

    lines = [
        f'📊 土地熱門搜尋趨勢｜{today.isoformat()}',
        f'資料來源：Google Trends（台灣）',
        '',
        '🔥 近 7 天熱搜排行（前 15 名）',
        '',
    ]

    for i, r in enumerate(hot, 1):
        trend = r['trend']
        kw = r['keyword']
        score = r['score']
        chg = r['change_pct']
        chg_str = f'+{chg:.0f}%' if chg >= 0 else f'{chg:.0f}%'
        lines.append(f'{i:>2}. {trend} {kw}　熱度 {score}　({chg_str})')

    if rising:
        lines += ['', '🚀 上升中關鍵字']
        for r in rising[:8]:
            lines.append(f'  {r["trend"]} {r["keyword"]}　{r["change_pct"]:+.0f}%')

    lines += [
        '',
        '📌 可用題材方向：Threads / Shorts / FB貼文 / YouTube標題',
    ]
    return '\n'.join(lines)


def save_json(rows: list[dict], today: date) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f'keyword_trends_{today.isoformat()}.json'
    payload = {
        'date': today.isoformat(),
        'source': 'Google Trends TW',
        'keywords': rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=data
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            return json.loads(r.read()).get('ok', False)
    except Exception as e:
        print(f'Telegram 發送失敗：{e}')
        return False


def main():
    parser = argparse.ArgumentParser(description='土地熱門搜尋趨勢週報')
    parser.add_argument('--dry', action='store_true', help='只印不存檔不推播')
    parser.add_argument('--push', action='store_true', help='存檔並推 Telegram')
    parser.add_argument('--date', default=None, help='指定日期 YYYY-MM-DD，預設今天')
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()

    print(f'🔍 抓取 Google Trends 土地關鍵字熱度（{today.isoformat()}）...')
    rows = fetch_trends(today)

    if not rows:
        print('無法取得資料（可能被 Google 暫時限流，稍後再試）')
        sys.exit(1)

    report = format_report(rows, today)
    print()
    print(report)

    if args.dry:
        return

    saved = save_json(rows, today)
    print(f'\n✅ 已存檔：{saved}')

    if args.push:
        env = load_env()
        token = env.get('TELEGRAM_BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
        chat_id = env.get('TELEGRAM_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID', '')
        if token and chat_id:
            ok = send_telegram(token, chat_id, report)
            print('✅ Telegram 推播成功' if ok else '❌ Telegram 推播失敗')
        else:
            print('未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過推播')


if __name__ == '__main__':
    main()
