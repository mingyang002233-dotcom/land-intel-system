#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
weekly_report.py — 老蕭 LAND 土地新聞週報

用法：
  python3 scripts/weekly_report.py
  python3 scripts/weekly_report.py --date 2026-05-20

只產生檔案，不推 Telegram，不發布社群。
"""

import argparse
import html
import json
import re
import shutil
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOCIAL_ROOT = PROJECT_ROOT / 'outputs' / 'social'
REVIEW_ROOT = SOCIAL_ROOT / 'review_queue'
WEEKLY_ROOT = SOCIAL_ROOT / 'weekly_report'

TOPIC_KEYWORDS = [
    '區段徵收', '市地重劃', '自辦重劃', '補償費', '抵價地', '配地',
    '土增稅', '都市計畫', '捷運', '高鐵', '台鐵', '科學園區',
    '南科', '竹科', '中科', '建商布局', '大型土地開發', '土地開發',
]

REGIONS = [
    '台北', '臺北', '新北', '桃園', '新竹', '苗栗', '台中', '臺中',
    '彰化', '南投', '雲林', '嘉義', '台南', '臺南', '高雄', '屏東',
    '宜蘭', '花蓮', '台東', '臺東', '基隆', '大園', '青埔', '南科',
    '永康', '學甲', '神岡', '社子島', '航空城',
]


def _date_range(end_date: date, days: int = 7) -> list[date]:
    start = end_date - timedelta(days=days - 1)
    return [start + timedelta(days=i) for i in range(days)]


def _load_candidates(day: date) -> list[dict]:
    day_dir = REVIEW_ROOT / day.isoformat()
    items = []
    for path in sorted(day_dir.glob('post_*/candidate_news.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        for item in data:
            item = dict(item)
            item['review_date'] = day.isoformat()
            item['review_folder'] = str(path.parent)
            items.append(item)
    return items


def _score(item: dict) -> tuple:
    return (
        item.get('laoxiao_score', 0),
        item.get('development_score', 0),
        item.get('social_score', 0),
        item.get('official_score', 0),
    )


def _source(item: dict) -> str:
    return item.get('src_name') or item.get('src_url') or '未標示來源'


def _url(item: dict) -> str:
    return item.get('link') or item.get('google_link') or ''


def _reason(item: dict) -> str:
    bucket = item.get('bucket') or '新聞池'
    score = item.get('laoxiao_score', 0)
    return f'老蕭分數 {score}；命中「{bucket}」；已通過市場資料排除規則。'


def _count_hits(items: list[dict], keywords: list[str]) -> Counter:
    c = Counter()
    for item in items:
        text = ' '.join([
            item.get('title', ''),
            item.get('bucket', ''),
            item.get('src_name', ''),
        ])
        for kw in keywords:
            if kw in text:
                c[kw] += 1
    return c


def _fmt_item_txt(item: dict, idx: int) -> str:
    return '\n'.join([
        f'{idx}. {item.get("title", "")}',
        f'   來源：{_source(item)}',
        f'   日期：{item.get("review_date", "")}',
        f'   分數：{item.get("laoxiao_score", 0)}',
        f'   理由：{_reason(item)}',
        f'   網址：{_url(item)}',
    ])


def _html_item(item: dict, idx: int) -> str:
    title = html.escape(item.get('title', ''))
    source = html.escape(_source(item))
    url = html.escape(_url(item))
    reason = html.escape(_reason(item))
    review_date = html.escape(item.get('review_date', ''))
    score = html.escape(str(item.get('laoxiao_score', 0)))
    link = f'<a href="{url}" target="_blank" rel="noopener">{url}</a>' if url else '未提供'
    return f'''
    <article class="item">
      <div class="rank">#{idx}</div>
      <h3>{title}</h3>
      <dl>
        <dt>來源</dt><dd>{source}</dd>
        <dt>日期</dt><dd>{review_date}</dd>
        <dt>老蕭分數</dt><dd>{score}</dd>
        <dt>入選理由</dt><dd>{reason}</dd>
        <dt>網址</dt><dd class="url">{link}</dd>
      </dl>
    </article>
    '''


def _render_txt(report_date: date, items: list[dict]) -> str:
    sorted_items = sorted(items, key=_score, reverse=True)
    top = sorted_items[:1]
    high = sorted_items[:10]
    kw = _count_hits(items, TOPIC_KEYWORDS).most_common(12)
    regions = _count_hits(items, REGIONS).most_common(10)

    lines = [
        f'老蕭 LAND 土地新聞週報｜{report_date.isoformat()}',
        f'資料期間：最近 7 天每日候選池',
        f'候選總數：{len(items)}',
        '',
        '一、上週最值得發的土地新聞',
    ]
    lines += [_fmt_item_txt(item, i) for i, item in enumerate(top, 1)]
    lines += ['', '二、上週高分候選新聞']
    lines += [_fmt_item_txt(item, i) for i, item in enumerate(high, 1)]
    lines += ['', '三、上週熱門土地開發關鍵字']
    lines += [f'{i}. {k}：{v} 次' for i, (k, v) in enumerate(kw, 1)] or ['（無）']
    lines += ['', '四、上週熱門區域／縣市']
    lines += [f'{i}. {k}：{v} 次' for i, (k, v) in enumerate(regions, 1)] or ['（無）']
    lines += ['', '五、上週主要題材']
    topic_hits = _count_hits(items, TOPIC_KEYWORDS)
    lines += [f'- {k}：{topic_hits.get(k, 0)}' for k in TOPIC_KEYWORDS]
    return '\n'.join(lines)


def _render_html(report_date: date, items: list[dict]) -> str:
    sorted_items = sorted(items, key=_score, reverse=True)
    top = sorted_items[:1]
    high = sorted_items[:10]
    kw = _count_hits(items, TOPIC_KEYWORDS).most_common(12)
    regions = _count_hits(items, REGIONS).most_common(10)
    topic_hits = _count_hits(items, TOPIC_KEYWORDS)

    top_html = ''.join(_html_item(item, i) for i, item in enumerate(top, 1))
    high_html = ''.join(_html_item(item, i) for i, item in enumerate(high, 1))
    kw_html = ''.join(f'<li>{html.escape(k)}：{v} 次</li>' for k, v in kw) or '<li>無</li>'
    region_html = ''.join(f'<li>{html.escape(k)}：{v} 次</li>' for k, v in regions) or '<li>無</li>'
    topic_html = ''.join(f'<li>{html.escape(k)}：{topic_hits.get(k, 0)}</li>' for k in TOPIC_KEYWORDS)

    return f'''<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>老蕭 LAND 土地新聞週報｜{report_date.isoformat()}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", sans-serif; background: #f6f7f9; color: #172033; line-height: 1.55; }}
    main {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 54px; }}
    h1 {{ margin: 0 0 6px; font-size: 30px; }}
    .meta {{ color: #667085; margin-bottom: 24px; }}
    h2 {{ margin-top: 34px; padding-bottom: 8px; border-bottom: 2px solid #d4af37; }}
    .item {{ background: #fff; border: 1px solid #dde3ec; border-radius: 8px; padding: 16px; margin: 14px 0; }}
    .rank {{ color: #8a6500; font-weight: 700; }}
    h3 {{ margin: 6px 0 12px; font-size: 19px; }}
    dl {{ display: grid; grid-template-columns: 92px 1fr; gap: 8px 12px; margin: 0; }}
    dt {{ font-weight: 700; color: #475467; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 18px; }}
    .box {{ background: #fff; border: 1px solid #dde3ec; border-radius: 8px; padding: 14px; }}
    a {{ color: #0b5cad; }}
  </style>
</head>
<body>
<main>
  <h1>老蕭 LAND 土地新聞週報</h1>
  <div class="meta">週報日期：{report_date.isoformat()}｜資料來源：最近 7 天每日候選池｜候選總數：{len(items)}</div>

  <h2>上週最值得發的土地新聞</h2>
  {top_html}

  <h2>上週高分候選新聞</h2>
  {high_html}

  <div class="grid">
    <section class="box">
      <h2>熱門土地開發關鍵字</h2>
      <ol>{kw_html}</ol>
    </section>
    <section class="box">
      <h2>熱門區域／縣市</h2>
      <ol>{region_html}</ol>
    </section>
  </div>

  <h2>主要題材</h2>
  <ul>{topic_html}</ul>
</main>
</body>
</html>
'''


def _cleanup_review_queue(keep_days: int = 30, today: date = None) -> None:
    today = today or date.today()
    if not REVIEW_ROOT.exists():
        return
    cutoff = today - timedelta(days=keep_days)
    for path in REVIEW_ROOT.iterdir():
        if not path.is_dir():
            continue
        try:
            d = date.fromisoformat(path.name)
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def build_weekly_report(report_date: date) -> tuple[Path, Path]:
    items = []
    for d in _date_range(report_date, 7):
        items.extend(_load_candidates(d))
    items = sorted(items, key=_score, reverse=True)

    WEEKLY_ROOT.mkdir(parents=True, exist_ok=True)
    html_path = WEEKLY_ROOT / f'{report_date.isoformat()}_land_news_weekly.html'
    txt_path = WEEKLY_ROOT / f'{report_date.isoformat()}_land_news_weekly.txt'

    if not items:
        txt = f'老蕭 LAND 土地新聞週報｜{report_date.isoformat()}\n最近 7 天無每日候選池資料。'
        html_doc = f'<!doctype html><meta charset="utf-8"><h1>老蕭 LAND 土地新聞週報</h1><p>最近 7 天無每日候選池資料。</p>'
    else:
        txt = _render_txt(report_date, items)
        html_doc = _render_html(report_date, items)

    txt_path.write_text(txt, encoding='utf-8')
    html_path.write_text(html_doc, encoding='utf-8')
    _cleanup_review_queue(today=report_date)
    return html_path, txt_path


def main():
    parser = argparse.ArgumentParser(description='老蕭 LAND 土地新聞週報')
    parser.add_argument('--date', default=None, help='週報日期 YYYY-MM-DD，預設今天')
    parser.add_argument('--monday', default=None, help='相容舊參數；等同 --date')
    args = parser.parse_args()
    target = date.fromisoformat(args.date or args.monday) if (args.date or args.monday) else date.today()
    html_path, txt_path = build_weekly_report(target)
    print(f'週報 HTML：{html_path}')
    print(f'週報 TXT：{txt_path}')


if __name__ == '__main__':
    main()
