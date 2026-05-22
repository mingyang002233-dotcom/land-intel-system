#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intel_rules.py — 土地情報規則分類
輸入 intel_591.py 的 diff 結果與 hot 結果，
輸出 A/B 級情報清單。不使用 AI，純規則。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 規則門檻 ─────────────────────────────────────────────
A_NEW_COUNT       = 10     # 同區新增 >= 此數 → A級
A_PRICE_DROP_PCT  = 10.0   # 降價幅度 >= 此% → A級
A_OWNER_SELL      = True   # 地主自售 → A級
A_LAND_PING       = 300    # 建地坪數 >= 此 → A級
A_INDUSTRY_PING   = 500    # 工業地坪數 >= 此 → A級


def _load_watchlist() -> dict:
    """讀 config/watchlist.yaml，無 pyyaml 就用簡單解析。"""
    wl_path = PROJECT_ROOT / 'config' / 'watchlist.yaml'
    result = {}
    if not wl_path.exists():
        return result
    current_city = None
    for line in wl_path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        if line.endswith(':') and not line.startswith(' '):
            current_city = line.rstrip(':').strip()
            result[current_city] = []
        elif line.strip().startswith('- ') and current_city:
            result[current_city].append(line.strip()[2:].strip())
    return result


def _in_watchlist(city: str, district: str, section: str, watchlist: dict) -> str | None:
    """回傳符合的關鍵字，否則 None。"""
    keywords = watchlist.get(city, [])
    for kw in keywords:
        if kw in (district or '') or kw in (section or ''):
            return kw
    return None


def _price_drop_pct(prev: float | None, curr: float | None) -> float:
    if not prev or not curr or prev <= 0:
        return 0.0
    return (prev - curr) / prev * 100


def classify_new_listing(row: dict, watchlist: dict) -> dict | None:
    """
    分類單筆新增刊登。
    回傳 {level, reason, score} 或 None（B 級以下忽略）。
    """
    city     = row.get('city', '')
    district = row.get('district', '')
    section  = row.get('section_raw', '')
    ltype    = row.get('land_type', '')
    area     = row.get('area_ping') or 0
    is_agent = row.get('is_agent', 1)
    price    = row.get('total_price_wan') or 0

    reasons = []
    score   = 10  # B 級基礎分

    # watchlist 加分
    kw = _in_watchlist(city, district, section, watchlist)
    if kw:
        score += 20
        reasons.append(f'監控區域：{kw}')

    # 地主自售
    if not is_agent:
        score += 30
        reasons.append('地主自售')

    # 大坪數
    if ltype == '建地' and area >= A_LAND_PING:
        score += 25
        reasons.append(f'建地大坪數（{area:.0f}坪）')
    elif ltype == '工業地' and area >= A_INDUSTRY_PING:
        score += 25
        reasons.append(f'工業地大坪數（{area:.0f}坪）')

    level = 'A' if score >= 50 else 'B'
    return {
        'level':  level,
        'reason': '、'.join(reasons) if reasons else '一般新上架',
        'score':  score,
        'row':    row,
    }


def classify_price_drop(row: dict, watchlist: dict) -> dict | None:
    """分類降價物件。"""
    prev  = row.get('prev_price') or 0
    curr  = row.get('total_price_wan') or 0
    pct   = _price_drop_pct(prev, curr)
    city  = row.get('city', '')
    dist  = row.get('district', '')
    sec   = row.get('section_raw', '')

    reasons = []
    score   = 10

    kw = _in_watchlist(city, dist, sec, watchlist)
    if kw:
        score += 20
        reasons.append(f'監控區域：{kw}')

    if pct >= A_PRICE_DROP_PCT:
        score += 35
        reasons.append(f'降價 {pct:.1f}%（{prev:.0f}→{curr:.0f}萬）')
    else:
        reasons.append(f'降價 {pct:.1f}%（{prev:.0f}→{curr:.0f}萬）')

    level = 'A' if score >= 50 else 'B'
    return {
        'level':  level,
        'reason': '、'.join(reasons),
        'score':  score,
        'row':    row,
    }


def classify_hot_zone(zone: dict, watchlist: dict) -> dict | None:
    """分類熱區（高刊登量）。"""
    city  = zone.get('city', '')
    dist  = zone.get('district', '')
    sec   = zone.get('section_raw', '')
    cnt   = zone.get('listing_count', 0)

    reasons = []
    score   = 0

    kw = _in_watchlist(city, dist, sec, watchlist)
    if kw:
        score += 20
        reasons.append(f'監控區域：{kw}')

    if cnt >= A_NEW_COUNT:
        score += 40
        reasons.append(f'刊登量達 {cnt} 筆')
    elif cnt >= 5:
        score += 15
        reasons.append(f'刊登量 {cnt} 筆')

    if score < 15:
        return None  # 不在 watchlist 且筆數少 → 略過

    level = 'A' if score >= 50 else 'B'
    return {
        'level':  level,
        'reason': '、'.join(reasons),
        'score':  score,
        'zone':   zone,
    }


def run_rules(diff: dict, hot_zones: list[dict]) -> dict:
    """
    統整 diff + hot_zones，回傳分類結果。

    回傳：
    {
      'A': [intel_item, ...],
      'B': [intel_item, ...],
      'hot_watchlist': [zone, ...],
    }
    """
    watchlist = _load_watchlist()
    result = {'A': [], 'B': [], 'hot_watchlist': []}

    # 新增刊登
    for row in diff.get('new', []):
        c = classify_new_listing(row, watchlist)
        if c:
            result[c['level']].append(c)

    # 降價
    for row in diff.get('down_price', []):
        c = classify_price_drop(row, watchlist)
        if c:
            result[c['level']].append(c)

    # 熱區（只回 watchlist 內的）
    for zone in hot_zones:
        c = classify_hot_zone(zone, watchlist)
        if c:
            result['hot_watchlist'].append(c)

    # 依 score 排序
    result['A'].sort(key=lambda x: -x['score'])
    result['B'].sort(key=lambda x: -x['score'])
    result['hot_watchlist'].sort(key=lambda x: -x['score'])

    return result
