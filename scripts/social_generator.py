#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
social_generator.py — 老蕭 LAND 土地戰情內容工廠 v2

用法：
  python3 scripts/social_generator.py          # 產生今日 1 則新聞候選
  python3 scripts/social_generator.py --dry    # 只印不存檔
  python3 scripts/social_generator.py --date 2026-05-19
  python3 scripts/social_generator.py --push-review # 產生後推 Telegram 審核
"""

import argparse
import html
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT  = PROJECT_ROOT / 'outputs' / 'social'
REVIEW_ROOT  = OUTPUT_ROOT / 'review_queue'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ── 字型 ──────────────────────────────────────────────────
_FONT_CANDIDATES = [
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/System/Library/Fonts/STHeiti Light.ttc',
    '/System/Library/Fonts/Helvetica.ttc',
]
_FONT_PATH = next((f for f in _FONT_CANDIDATES if Path(f).exists()), None)

# ── 設計常數 ──────────────────────────────────────────────
DARK_BLUE  = (13,  27,  42)
NAVY       = (22,  43,  68)
GOLD       = (212, 175, 55)
GOLD_LIGHT = (240, 210, 100)
WHITE      = (255, 255, 255)
GRAY       = (160, 160, 170)
RED_ALERT  = (180, 40,  40)

# ── 新聞主題定義 ──────────────────────────────────────────
# 這些是搜尋來源桶，輸出時會合併成同一個「土地開發新聞」候選池。
# 591、實價登錄、radar、listings 留在獨立流程。
_TOPICS = [
    {
        'id':       '政府公告',
        'queries':  ['土地開發 政府公告', '都市計畫 公告', '土地徵收 公告'],
        'label':    '政府公告',
        'why':      '政府公告是土地開發流程中最具效力的訊號，包含公開展覽、核定、徵收、重劃與重大建設程序，會影響土地使用條件與開發時程。',
        'impact':   '觀察公告日期、法定程序階段、主管機關與後續審議節點；不推論受惠區、不推估行情。',
        'platform': 'FB + IG',
        'hashtags': ['#政府公告', '#土地政策', '#都市計畫', '#土地開發', '#老蕭LAND'],
    },
    {
        'id':       '土地開發',
        'queries':  ['土地開發 新聞', '產業園區 開發', '重大開發案 土地'],
        'label':    '土地開發新聞',
        'why':      '土地開發新聞能反映政府或大型公共建設的方向，包含產業園區、交通建設與整體開發計畫，是判斷區域長線題材的重要來源。',
        'impact':   '觀察開發案是否已有主管機關、法定公告、核定文件、招商或動工時程；未確認前不延伸區域受惠判斷。',
        'platform': 'FB（在地社群）+ IG',
        'hashtags': ['#土地開發', '#產業園區', '#公共建設', '#區域發展', '#土地政策'],
    },
    {
        'id':       '都市計畫',
        'queries':  ['都市計畫 變更', '都市計畫 公告', '國土計畫 土地'],
        'label':    '都市計畫變更',
        'why':      '都市計畫變更是土地使用分區與公共設施配置調整的法定程序，從公開展覽到核定都可能改變土地可利用強度。',
        'impact':   '觀察計畫名稱、公開展覽或核定階段、土地使用分區變更內容與主管機關文件；不推論價格結果。',
        'platform': 'FB + IG',
        'hashtags': ['#都市計畫', '#土地變更', '#國土計畫', '#使用分區', '#土地政策'],
    },
    {
        'id':       '區段徵收',
        'queries':  ['區段徵收', '土地徵收補償', '重劃徵收'],
        'label':    '區段徵收政策',
        'why':      '區段徵收涉及土地取得、補償、抵價地與公共設施開闢程序，需以主管機關公告與法定文件為準。',
        'impact':   '觀察徵收範圍、補償基準、抵價地比例、聽證或公告期程；不推論周邊行情。',
        'platform': 'FB + IG',
        'hashtags': ['#區段徵收', '#土地政策', '#徵收補償', '#都市計畫', '#土地法規'],
    },
    {
        'id':       '市地重劃',
        'queries':  ['市地重劃', '自辦重劃', '市地重劃公告'],
        'label':    '市地重劃動態',
        'why':      '市地重劃涉及土地交換分合、公共設施負擔、重劃負擔與分配結果，需追蹤公告與審議進度。',
        'impact':   '觀察重劃範圍、負擔比例、分配草案、異議期與主管機關公告；不推論價格或交易時機。',
        'platform': 'FB + IG',
        'hashtags': ['#市地重劃', '#自辦重劃', '#重劃區', '#都市發展', '#土地政策'],
    },
    {
        'id':       '交通建設',
        'queries':  ['捷運 建設 土地', '交通建設 土地開發', '捷運聯開 公告'],
        'label':    '捷運/交通建設',
        'why':      '捷運與交通建設會改變可達性與生活圈邊界，聯合開發、站區開發與路網核定都會牽動周邊土地使用價值。',
        'impact':   '觀察路線核定、站點位置公告、環評、用地取得、招標與動工節點；不標示未確認的精準受惠位置。',
        'platform': 'FB + IG',
        'hashtags': ['#捷運建設', '#交通建設', '#聯合開發', '#公共建設', '#土地開發'],
    },
]

_TOPIC_MAP = {t['id']: t for t in _TOPICS}

_DAILY_NEWS_TOPIC = {
    'id':       '每日新聞',
    'label':    '每日土地新聞主文',
    'why':      '本則主文是從今日共同新聞池排序後選出的候選，來源範圍包含政府公告、都市計畫、區段徵收、市地重劃與捷運/交通建設。',
    'impact':   '只整理來源事實、發布時間與程序節點；不推論受惠區、不推估行情、不標示未確認精準位置。',
    'platform': 'FB + IG',
    'hashtags': ['#土地開發新聞', '#政府公告', '#都市計畫', '#區段徵收', '#交通建設'],
}

_BASE_HASHTAGS = ['#老蕭LAND', '#土地情報', '#土地政策', '#土地戰情']

_EXCLUDE_KEYWORDS = [
    '591', '實價登錄', '成交行情', '成交價', '成交單價', '實價',
    '出售', '待售', '售地', '租售', '開價', '行情', '物件',
    '房仲', '房產', '買地', '投資', '建商', '開發商',
    '前景看俏', '搶攻', '商機', '吸金', '房價', '住宅規劃',
    '地王', '每坪',
    '好房網', '台灣房屋', '住展', '樂居', '591房屋',
    '法拍', '標售土地',
    'radar', 'listing', 'listings',
]

_OFFICIAL_SOURCE_MARKERS = [
    '.gov.tw', 'gov.tw', '政府', '地政局', '都發局', '都市發展局',
    '交通部', '內政部', '國土管理署', '市政府', '縣政府', '捷運局',
]

_QUALITY_RULES = [
    '禁止 AI 腦補受惠區。',
    '禁止模糊投資暗示。',
    '禁止未確認行情。',
    '必須附資料來源。',
    '圖卡為生成摘要時必須標示「示意圖」。',
    '圖文不得出現不精準地圖位置；若無官方座標，不標示精準位置。',
]

_GRADE_LABELS = {
    'S': 'S級：官方公告',
    'A': 'A級：多來源新聞',
    'B': 'B級：市場消息',
    'C': 'C級：未確認消息（預設不發）',
}

_LAOXIAO_KEYWORDS = {
    '區段徵收': 35, '市地重劃': 34, '自辦重劃': 34, '重劃': 28,
    '補償費': 34, '抵價地': 34, '配地': 30, '捷運聯開': 32,
    '聯合開發': 28, '高鐵': 26, '台鐵': 24, '科學園區': 32,
    '南科': 34, '竹科': 32, '中科': 32, '產業園區': 24,
    '都市計畫': 24, '重大變更': 26, '大型土地開發': 30,
    '土地開發': 22, '開發區': 22, '公辦開發': 24,
}

_DEVELOPMENT_KEYWORDS = {
    '區段徵收': 30, '市地重劃': 28, '自辦重劃': 28,
    '補償費': 28, '抵價地': 28, '配地': 24, '土地開發': 22,
    '聯合開發': 24, '捷運': 20, '高鐵': 20, '台鐵': 18,
    '科學園區': 28, '產業園區': 22, '都市計畫': 18,
    '開發案': 18, '用地取得': 18, '動工': 12,
}

_SOCIAL_KEYWORDS = {
    '補償費': 18, '所有權人': 16, '地主': 16, '公聽會': 14,
    '徵求意見': 12, '聽證': 12, '公告': 8, '核定': 8,
    '開標': 6, '招商': 6,
}

_LOW_VALUE_KEYWORDS = {
    '圖書館': 22, '機關用地': 16, '布告欄': 20, '紙圖': 14,
    '小型': 10, '例行': 10, '標售': 10,
}


# ═══════════════════════════════════════════════════════════
# 新聞抓取
# ═══════════════════════════════════════════════════════════

def _fetch_news() -> dict[str, list[dict]]:
    """回傳 {topic_id: [news_item, ...]}"""
    BASE = 'https://news.google.com/rss/search?hl=zh-TW&gl=TW&ceid=TW:zh-Hant&q='
    result: dict[str, list[dict]] = defaultdict(list)
    seen_titles: set[str] = set()

    for topic in _TOPICS:
        tid = topic['id']
        for q in topic['queries']:
            try:
                req = urllib.request.Request(
                    BASE + urllib.parse.quote(q),
                    headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=8) as r:
                    body = r.read().decode('utf-8', errors='ignore')
                for raw in re.findall(r'<item>([\s\S]*?)</item>', body)[:6]:
                    title = (re.findall(
                        r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', raw
                    ) or [''])[0].strip()
                    link = (re.findall(r'<link>(.*?)</link>', raw) or
                            re.findall(r'<link/>(.*?)<', raw) or [''])[0].strip()
                    pub  = (re.findall(r'<pubDate>(.*?)</pubDate>', raw) or [''])[0].strip()
                    src_name = (re.findall(
                        r'<source[^>]*>([^<]+)</source>', raw) or [''])[0].strip()
                    src_url  = (re.findall(
                        r'<source url="([^"]+)"', raw) or [''])[0].strip()

                    title = html.unescape(title)
                    link = html.unescape(link)
                    pub = html.unescape(pub)
                    src_name = html.unescape(src_name)
                    src_url = html.unescape(src_url)
                    resolved_link = _resolve_news_url(link)
                    haystack = ''.join([title, link, src_name, src_url])
                    if any(k.lower() in haystack.lower() for k in _EXCLUDE_KEYWORDS):
                        continue

                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        result[tid].append({
                            'title':    title,
                            'link':     resolved_link or link,
                            'google_link': link,
                            'pub':      pub,
                            'src_name': src_name,
                            'src_url':  src_url,
                        })
            except Exception:
                pass
    return result


def _resolve_news_url(url: str) -> str:
    if not url:
        return ''
    if 'news.google.com/rss/articles/' not in url and 'news.google.com/articles/' not in url:
        return url
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0'},
            method='GET',
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=6) as r:
            final_url = r.geturl()
        return final_url or url
    except Exception:
        return url


# ═══════════════════════════════════════════════════════════
# 新聞池評分
# ═══════════════════════════════════════════════════════════

_WEIGHT = {
    '政府公告': 5, '土地開發': 4, '都市計畫': 5,
    '區段徵收': 5, '市地重劃': 4, '交通建設': 4,
}

def _rank_topics(news_map: dict[str, list[dict]]) -> list[dict]:
    ranked = []
    for topic in _TOPICS:
        tid = topic['id']
        items = news_map.get(tid, [])
        if not items:
            continue
        score = len(items) * _WEIGHT.get(tid, 2)
        ranked.append({
            'topic': topic,
            'news':  items[:4],   # 最多用 4 則
            'score': score,
            'count': len(items),
        })
    return sorted(ranked, key=lambda x: -x['score'])


def _sum_keyword_score(text: str, weights: dict[str, int]) -> int:
    return sum(score for keyword, score in weights.items() if keyword in text)


def _score_item(item: dict) -> dict:
    text = ' '.join([
        item.get('title', ''),
        item.get('src_name', ''),
        item.get('src_url', ''),
        item.get('bucket', ''),
    ])
    development_score = _sum_keyword_score(text, _DEVELOPMENT_KEYWORDS)
    social_score = _sum_keyword_score(text, _SOCIAL_KEYWORDS)
    official_score = 12 if _is_official_source(item) else 0
    low_value_penalty = _sum_keyword_score(text, _LOW_VALUE_KEYWORDS)
    laoxiao_score = (
        _sum_keyword_score(text, _LAOXIAO_KEYWORDS)
        + development_score
        + social_score
        + official_score
        - low_value_penalty
    )
    return {
        'laoxiao_score': laoxiao_score,
        'development_score': development_score,
        'social_score': social_score,
        'official_score': official_score,
        'low_value_penalty': low_value_penalty,
    }


def _candidate_score(item: dict) -> tuple:
    scores = _score_item(item)
    return (
        scores['laoxiao_score'],
        scores['development_score'],
        scores['social_score'],
        scores['official_score'],
    )


def _build_daily_entry(news_map: dict[str, list[dict]], candidate_limit: int = 5) -> dict | None:
    items = []
    seen_titles = set()
    for topic in _TOPICS:
        topic_id = topic['id']
        for item in news_map.get(topic_id, []):
            title = item.get('title', '')
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            enriched = dict(item)
            enriched['bucket'] = topic_id
            enriched.update(_score_item(enriched))
            items.append(enriched)

    if not items:
        return None

    ranked_items = sorted(items, key=_candidate_score, reverse=True)
    candidates = ranked_items[:candidate_limit]
    for item in candidates:
        item['link'] = _resolve_candidate_url(item)
    main = candidates[0]
    return {
        'topic': _DAILY_NEWS_TOPIC,
        'news': [main],
        'main': main,
        'candidates': candidates,
        'score': len(items),
        'count': len(items),
        'buckets': sorted({item['bucket'] for item in ranked_items}),
    }


def _resolve_candidate_url(item: dict) -> str:
    link = item.get('link', '')
    if link and 'news.google.com/rss/articles/' not in link and 'news.google.com/articles/' not in link:
        return link

    source_url = item.get('src_url', '')
    domain = urllib.parse.urlparse(source_url).netloc or item.get('src_name', '')
    query_title = re.sub(r'\s+-\s+[^-]+$', '', item.get('title', '')).strip()
    if not domain or not query_title:
        return link

    query = f'site:{domain} {query_title}'
    try:
        url = 'https://duckduckgo.com/html/?q=' + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=8) as r:
            body = r.read().decode('utf-8', errors='ignore')
        matches = re.findall(r'class="result__a" href="([^"]+)"', body)
        for raw in matches:
            raw = html.unescape(raw)
            parsed = urllib.parse.urlparse(raw)
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get('uddg', [raw])[0]
            if domain in urllib.parse.urlparse(target).netloc:
                return target
    except Exception:
        pass
    return link


def _is_official_source(item: dict) -> bool:
    haystack = ' '.join([
        item.get('src_name', ''),
        item.get('src_url', ''),
        item.get('link', ''),
    ])
    return any(marker in haystack for marker in _OFFICIAL_SOURCE_MARKERS)


def _quality_profile(entry: dict) -> dict:
    news = entry.get('news', [])
    sources = sorted({
        n.get('src_name') or n.get('src_url') or n.get('link') or '未標示來源'
        for n in news
    })
    has_source = all((n.get('src_name') or n.get('src_url') or n.get('link')) for n in news)
    official_count = sum(1 for n in news if _is_official_source(n))
    has_official = official_count > 0
    source_count = len(sources)

    if has_official and official_count == len(news):
        grade = 'S'
        publish = 'YES'
        reason = '全部來源皆為官方公告或主管機關來源。'
    elif has_source and source_count >= 2:
        grade = 'A'
        publish = 'YES'
        reason = '具多來源新聞交叉參照。'
    elif has_source:
        grade = 'B'
        publish = 'NO'
        reason = '來源不足，先留在 review queue。'
    else:
        grade = 'C'
        publish = 'NO'
        reason = '缺少可核對資料來源，預設不發。'

    return {
        'grade': grade,
        'grade_label': _GRADE_LABELS[grade],
        'publish': publish,
        'reason': reason,
        'sources': sources,
        'source_count': source_count,
        'has_official': has_official,
        'rules': _QUALITY_RULES,
        'is_illustration': True,
        'location_precision': '未標示精準地圖位置',
    }


def _candidate_reason(item: dict) -> str:
    parts = []
    if item.get('laoxiao_score') is not None:
        parts.append(f'老蕭分數 {item.get("laoxiao_score")}')
    if _is_official_source(item):
        parts.append('來源屬官方或主管機關網域')
    elif item.get('src_name') or item.get('src_url') or item.get('link'):
        parts.append('具可核對新聞來源')
    else:
        parts.append('來源不足，需人工確認')

    bucket = item.get('bucket')
    if bucket:
        parts.append(f'命中「{bucket}」新聞篩選條件')
    parts.append('已通過市場資料排除規則')
    return '；'.join(parts) + '。'


def _gen_candidate_summary(entry: dict, today: date) -> str:
    lines = [
        f'每日土地新聞候選摘要｜{today.strftime("%Y/%m/%d")}',
        f'新聞池總數：{entry["count"]} 則',
        f'來源篩選桶：{", ".join(entry.get("buckets", [])) or "每日新聞池"}',
        '',
        '今日入選主文：',
    ]
    main = entry.get('main') or entry['news'][0]
    lines.append(f'1. {main["title"]}')
    lines.append(f'   來源：{main.get("src_name") or main.get("src_url") or "未標示來源"}')
    lines.append(f'   網址：{main.get("link") or ""}')
    lines.append(f'   入選理由：{_candidate_reason(main)}')
    lines += ['', '其他候選新聞：']
    for i, n in enumerate(entry.get('candidates', [])[1:], 1):
        lines.append(f'{i}. {n["title"]}')
        lines.append(f'   來源：{n.get("src_name") or n.get("src_url") or "未標示來源"}')
        lines.append(f'   網址：{n.get("link") or ""}')
        lines.append(f'   排序理由：{_candidate_reason(n)}')
    lines += ['', '正式圖文輸出：post_01_每日主文']
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════
# 文案生成
# ═══════════════════════════════════════════════════════════

def _gen_fb(entry: dict, today: date) -> str:
    topic  = entry['topic']
    news   = entry['news']
    label  = topic['label']
    count  = entry['count']
    quality = _quality_profile(entry)

    lines = [
        f'【土地戰情快訊｜{label}】',
        f'情報等級：{quality["grade_label"]}',
        f'建議發佈：{quality["publish"]}',
        '圖像標示：示意圖，非精準地圖位置',
        '',
        f'📡 今日新聞池共 {count} 則候選；本篇為排序後入選主文。',
        '',
        '📰 今日入選主文',
    ]
    main = entry.get('main') or news[0]
    lines.append(main['title'])
    if main.get('src_name'):
        lines.append(f'（來源：{main["src_name"]}）')

    lines += [
        '',
        '🔍 入選理由',
        _candidate_reason(main),
        '',
        '🔍 為什麼值得審核',
        topic['why'],
        '',
        '📌 觀察重點',
        topic['impact'],
        '',
        '🧭 品質規則',
        '本內容不推論受惠區、不提供行情判斷、不作投資暗示；請以來源原文與主管機關公告為準。',
        '',
        '─────────────────────────',
        f'📅 {today.strftime("%Y/%m/%d")} 老蕭 LAND 戰情室',
        '狀態：review queue，尚未發布。',
    ]

    # 附新聞連結
    links = [main] if main.get('link') else []
    if links:
        lines += ['', '🔗 相關新聞連結']
        for n in links:
            lines.append(n['link'])

    return '\n'.join(lines)


def _gen_ig(entry: dict, today: date) -> str:
    topic = entry['topic']
    news  = entry['news']
    label = topic['label']
    count = entry['count']

    lines = [
        f'🔔 {label}｜土地戰情快訊',
        f'情報等級：{_quality_profile(entry)["grade_label"]}',
        '圖像：示意圖，非精準地圖位置',
        '',
        f'今日新聞池 {count} 則候選，主文先進 review queue 👇',
        '',
    ]
    main = entry.get('main') or news[0]
    title = main['title']
    lines.append(f'▸ {title[:40]}{"…" if len(title)>40 else ""}')

    lines += [
        '',
        topic['why'][:80] + '…',
        '',
        '完整戰情 → 老蕭 LAND 主頁',
    ]
    return '\n'.join(lines)


def _gen_hashtags(topic: dict) -> str:
    tags = list(topic['hashtags']) + _BASE_HASHTAGS
    return ' '.join(dict.fromkeys(tags))


def _gen_source(entry: dict, today: date) -> str:
    topic = entry['topic']
    news  = entry['news']
    quality = _quality_profile(entry)
    lines = [
        f'主題：{topic["label"]}',
        f'日期：{today.strftime("%Y-%m-%d")}',
        f'情報等級：{quality["grade_label"]}',
        f'是否建議發佈：{quality["publish"]}',
        f'分級理由：{quality["reason"]}',
        f'圖像標示：示意圖，非精準地圖位置',
        f'新聞數量：{entry["count"]} 則',
        f'來源篩選桶：{", ".join(entry.get("buckets", [])) or "每日新聞池"}',
        f'排序分數：{entry["score"]}',
        '',
        '今日入選主文：',
        f'・{(entry.get("main") or news[0])["title"]}',
        f'  入選理由：{_candidate_reason(entry.get("main") or news[0])}',
        '',
        '其他候選新聞：',
    ]
    for n in entry.get('candidates', [])[1:]:
        lines.append(f'・{n["title"]}')
        if n.get('src_name'):
            lines.append(f'  來源媒體：{n["src_name"]}')
    lines += [
        '',
        '內容品質規則：',
    ]
    lines += [f'・{rule}' for rule in _QUALITY_RULES]
    lines += [
        '',
        '新聞來源：',
    ]
    for n in news:
        lines.append(f'・{n["title"]}')
        if n.get('src_name'):
            lines.append(f'  來源媒體：{n["src_name"]}')
        if n.get('link'):
            lines.append(f'  連結：{n["link"]}')
        lines.append('')
    return '\n'.join(lines)


def _gen_telegram_preview(entry: dict, today: date) -> str:
    news = entry['news']
    quality = _quality_profile(entry)
    published_at = datetime.now().strftime('%Y/%m/%d %H:%M')
    lines = [
        f'📰 老蕭 LAND｜每日新聞 Review',
        f'情報等級：{quality["grade_label"]}',
        f'發布時間：{published_at}',
        f'是否建議發佈：{quality["publish"]}',
        f'圖像標示：示意圖，非精準地圖位置',
        f'新聞池：{entry["count"]} 則，保留前 {len(entry.get("candidates", []))} 則候選',
        '',
        '今日入選主文：',
    ]
    main = entry.get('main') or news[0]
    line = main['title']
    lines.append(line)
    lines.append(f'來源：{main.get("src_name") or main.get("src_url") or "未標示來源"}')
    if main.get('link'):
        lines.append(main['link'])
    lines += [
        '',
        '入選理由：',
        _candidate_reason(main),
        '',
        '其他候選：',
    ]
    for i, n in enumerate(entry.get('candidates', [])[1:], 1):
        source = f'（{n["src_name"]}）' if n.get('src_name') else ''
        lines.append(f'{i}. {n["title"]}{source}')
    lines += [
        '',
        '詳細索引：',
    ]
    lines.append(str((REVIEW_ROOT / today.strftime('%Y-%m-%d') / 'post_01_每日主文' / 'daily_news_index.html')))
    lines += [
        '',
        '狀態：review queue，未自動發布 FB / IG。',
    ]
    return '\n'.join(lines)


def _gen_daily_news_index(entry: dict, today: date) -> str:
    def esc(value: str) -> str:
        return html.escape(value or '')

    def block(item: dict, label: str) -> str:
        title = esc(item.get('title', ''))
        source = esc(item.get('src_name') or item.get('src_url') or '未標示來源')
        pub = esc(item.get('pub') or '未標示')
        url = esc(item.get('link') or '')
        reason = esc(_candidate_reason(item))
        grade = esc(_quality_profile({'news': [item]})['grade_label'])
        score = esc(str(item.get('laoxiao_score', '')))
        link_html = f'<a href="{url}" target="_blank" rel="noopener">{url}</a>' if url else '未提供'
        return f'''
        <article class="item">
          <div class="label">{esc(label)}</div>
          <h2>{title}</h2>
          <dl>
            <dt>來源</dt><dd>{source}</dd>
            <dt>發布時間</dt><dd>{pub}</dd>
            <dt>情報等級</dt><dd>{grade}</dd>
            <dt>老蕭分數</dt><dd>{score}</dd>
            <dt>入選理由</dt><dd>{reason}</dd>
            <dt>完整網址</dt><dd class="url">{link_html}</dd>
          </dl>
        </article>
        '''

    main = entry.get('main') or entry['news'][0]
    others = entry.get('candidates', [])[1:]
    other_html = '\n'.join(block(item, f'候選 #{idx}') for idx, item in enumerate(others, 1))
    return f'''<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日土地新聞索引｜{today.strftime("%Y/%m/%d")}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", sans-serif; line-height: 1.55; color: #14213d; background: #f6f7f9; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 28px 18px 48px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    .meta {{ color: #596579; margin-bottom: 22px; }}
    .section {{ margin-top: 28px; font-size: 20px; border-bottom: 2px solid #d4af37; padding-bottom: 6px; }}
    .item {{ background: #fff; border: 1px solid #dde2ea; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    .label {{ display: inline-block; color: #8a6500; font-weight: 700; margin-bottom: 6px; }}
    h2 {{ font-size: 20px; margin: 4px 0 14px; }}
    dl {{ display: grid; grid-template-columns: 96px 1fr; gap: 8px 12px; margin: 0; }}
    dt {{ font-weight: 700; color: #4b5565; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    .url a {{ color: #0b5cad; }}
  </style>
</head>
<body>
<main>
  <h1>每日土地新聞索引</h1>
  <div class="meta">日期：{today.strftime("%Y/%m/%d")}｜新聞池：{entry["count"]} 則｜保留候選：{len(entry.get("candidates", []))} 則</div>

  <div class="section">今日主文</div>
  {block(main, '今日主文')}

  <div class="section">其他候選</div>
  {other_html}
</main>
</body>
</html>
'''


def _cleanup_review_queue(keep_days: int = 30, today: date = None) -> None:
    today = today or date.today()
    if not REVIEW_ROOT.exists():
        return
    cutoff = today - timedelta(days=keep_days)
    import shutil
    for path in REVIEW_ROOT.iterdir():
        if not path.is_dir():
            continue
        try:
            d = date.fromisoformat(path.name)
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(path, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 圖卡生成（新聞戰情風格）
# ═══════════════════════════════════════════════════════════

def _make_card(entry: dict, out_path: Path, card_type: str = 'cover',
               today: date = None) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print('[WARN] Pillow 未安裝，略過圖卡')
        return False

    today = today or date.today()
    topic = entry['topic']
    news  = entry['news']
    label = topic['label']

    W, H = (1080, 1080) if card_type == 'post' else (1080, 608)
    img  = Image.new('RGB', (W, H), DARK_BLUE)
    draw = ImageDraw.Draw(img)

    def _font(size: int):
        if _FONT_PATH:
            try:
                return ImageFont.truetype(_FONT_PATH, size)
            except Exception:
                pass
        return ImageFont.load_default()

    # ── 邊框 ──
    draw.rectangle([0, 0, W, 6],   fill=GOLD)
    draw.rectangle([0, H-6, W, H], fill=GOLD)
    draw.rectangle([0, 0, 6, H],   fill=GOLD)
    draw.rectangle([W-6, 0, W, H], fill=GOLD)

    # ── 警示色頂欄 ──
    draw.rectangle([0, 6, W, 60], fill=(20, 35, 58))
    draw.text((20, 12), 'LAND 土地戰情快訊', font=_font(28), fill=GOLD)
    draw.text((W-220, 14), today.strftime('%Y/%m/%d'), font=_font(26), fill=GRAY)

    # ── 主標題（主題）──
    headline_y = 80 if card_type == 'cover' else 90
    draw.text((30, headline_y), label, font=_font(88), fill=WHITE)

    # 金底線
    line_y = headline_y + 96
    draw.rectangle([30, line_y, min(30 + len(label)*60, W-30), line_y+5], fill=GOLD)

    # ── 新聞條目 ──
    news_y = line_y + 30
    news_font = _font(28 if card_type == 'cover' else 30)
    for n in news[:3 if card_type == 'cover' else 4]:
        title = n['title']
        short = title[:26] + '…' if len(title) > 26 else title
        draw.text((30, news_y), f'- {short}', font=news_font, fill=GRAY)
        news_y += 44
        if news_y > H - 120:
            break

    # ── 底部說明（post 才顯示 why 摘要）──
    if card_type == 'post' and news_y < H - 140:
        why_short = topic['why'][:42] + '…'
        draw.rectangle([20, news_y + 10, W-20, news_y + 60], fill=NAVY)
        draw.text((30, news_y + 16), why_short, font=_font(24), fill=GOLD_LIGHT)

    # ── 品牌 ──
    draw.rectangle([0, H-56, W, H-6], fill=(10, 20, 36))
    draw.text((30, H-46), '老蕭 LAND｜土地情報戰情室', font=_font(24), fill=GOLD)
    draw.text((W-430, H-46), '示意圖｜非精準位置', font=_font(22), fill=GRAY)
    draw.text((W-160, H-46), f'#{entry["count"]}篇新聞', font=_font(22), fill=GRAY)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), 'PNG')
    return True


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def generate(target_date: date = None, dry: bool = False,
             push_review_enabled: bool = False):
    d   = target_date or date.today()
    out = REVIEW_ROOT / d.strftime('%Y-%m-%d')

    print(f'\n🏭 老蕭 LAND 土地戰情內容工廠  ─  {d.strftime("%Y/%m/%d")}\n')

    # 清除同日舊資料夾，避免推播混入上次結果
    if not dry and out.exists():
        import shutil
        for old in sorted(out.glob('post_*')):
            shutil.rmtree(old, ignore_errors=True)

    print('  抓取新聞中...')

    news_map = _fetch_news()
    total    = sum(len(v) for v in news_map.values())
    print(f'  新聞：共 {total} 則（{len(news_map)} 個主題有資料）\n')

    daily_entry = _build_daily_entry(news_map)

    if not daily_entry:
        print('  ⚠️  今日無足夠新聞資料，請稍後重試')
        return

    selected = [daily_entry]

    print('📋 今日推薦發文 1 則\n')
    print('=' * 60)

    for rank, entry in enumerate(selected, 1):
        topic  = entry['topic']
        label  = topic['label']
        score  = entry['score']
        folder = out / f'post_{rank:02d}_每日主文'

        src_txt = _gen_source(entry, d)
        tg_txt  = _gen_telegram_preview(entry, d)
        cand_txt = _gen_candidate_summary(entry, d)
        index_html = _gen_daily_news_index(entry, d)
        main_txt = '\n'.join([
            f'每日土地新聞主文｜{d.strftime("%Y/%m/%d")}',
            '',
            (entry.get('main') or entry['news'][0])['title'],
            f'來源：{(entry.get("main") or entry["news"][0]).get("src_name") or (entry.get("main") or entry["news"][0]).get("src_url") or "未標示來源"}',
            f'網址：{(entry.get("main") or entry["news"][0]).get("link") or ""}',
            '',
            f'入選理由：{_candidate_reason(entry.get("main") or entry["news"][0])}',
        ])
        quality = _quality_profile(entry)

        print(f'\n【第 {rank} 則｜{label}｜新聞 {entry["count"]} 則｜{topic["platform"]}】')
        print(f'  {"─"*50}')
        for line in tg_txt.splitlines()[:14]:
            print(f'  {line}')
        print(f'  ...')

        if dry:
            print(f'  [DRY] 略過存檔')
            continue

        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'source.txt').write_text(src_txt, encoding='utf-8')
        (folder / 'telegram_preview.txt').write_text(tg_txt, encoding='utf-8')
        (folder / 'candidate_summary.txt').write_text(cand_txt, encoding='utf-8')
        (folder / 'daily_news_index.html').write_text(index_html, encoding='utf-8')
        (folder / 'main_draft.txt').write_text(main_txt, encoding='utf-8')
        (folder / 'candidate_news.json').write_text(
            json.dumps(entry.get('candidates', []), ensure_ascii=False, indent=2),
            encoding='utf-8')
        (folder / 'quality.json').write_text(
            json.dumps({
                'topic': topic['id'],
                'grade': quality['grade'],
                'grade_label': quality['grade_label'],
                'publish_recommendation': quality['publish'],
                'reason': quality['reason'],
                'sources': quality['sources'],
                'candidate_count': len(entry.get('candidates', [])),
                'candidates': [
                    {
                        'title': item.get('title', ''),
                        'source': item.get('src_name') or item.get('src_url') or '',
                        'bucket': item.get('bucket', ''),
                        'reason': _candidate_reason(item),
                    }
                    for item in entry.get('candidates', [])
                ],
                'rules': quality['rules'],
                'is_illustration': quality['is_illustration'],
                'location_precision': quality['location_precision'],
            }, ensure_ascii=False, indent=2),
            encoding='utf-8')

        print(f'\n  ✅ 存至：{folder}')
        print(f'  檔案：telegram_preview.txt / candidate_summary.txt / daily_news_index.html / main_draft.txt / candidate_news.json / quality.json')

    print('\n' + '=' * 60)
    if not dry:
        _cleanup_review_queue(today=d)
        print(f'\n📁 輸出資料夾：{out}')
        if push_review_enabled:
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from push_social_review import push_review
                print('\n📲 推播至 Telegram 審核...')
                push_review(target_date=d)
            except Exception as e:
                print(f'  [WARN] Telegram 推播失敗：{e}')
        else:
            print('\n📲 未推播 Telegram；所有內容已進 review queue。')
    print('\n⚠️  以上為候選內容，請審核後再發佈。\n')


def main():
    parser = argparse.ArgumentParser(description='老蕭 LAND 土地戰情內容工廠')
    parser.add_argument('--dry',  action='store_true', help='只預覽不存檔')
    parser.add_argument('--date', default=None, help='指定日期 YYYY-MM-DD')
    parser.add_argument('--push-review', action='store_true',
                        help='產生後推播至 Telegram 審核；預設不推播')
    args = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else None
    generate(target_date=target, dry=args.dry,
             push_review_enabled=args.push_review)


if __name__ == '__main__':
    main()
