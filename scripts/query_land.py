#!/usr/bin/env python3

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config' / 'realprice_config.json'
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'

sys.path.insert(0, str(Path(__file__).parent))
from parse_realprice import has_pua_chars, normalize_pua  # noqa: E402

CATEGORY_KEYWORDS = [
    ('特殊', ['贈與', '繼承', '交換', '親友', '特殊關係', '異常', '協議價購', '政府機關標讓售', '容積代金基金採購', '未登記建物']),
    ('車位', ['車位']),
    ('建地', ['建地']),
    ('農地', ['農地', '農業']),
    ('工業', ['工業', '乙工', '丁工', '甲工']),
    ('房屋', ['房屋', '建物', '成屋', '房地', '住家', '住宅']),
    ('土地', ['土地']),
]

PERIOD_PATTERNS = {
    '近一個月': 1,
    '近二個月': 2,
    '近四個月': 4,
    '近三個月': 3,
    '近半年': 6,
    '近六個月': 6,
    '近一年': 12,
    '一年內': 12,
    '今年': 'year'
}

CITY_ALIASES = {
    '台北': '台北市',
    '台北市': '台北市',
    '新北': '新北市',
    '新北市': '新北市',
    '桃園': '桃園市',
    '桃園市': '桃園市',
    '台中': '台中市',
    '台中市': '台中市',
    '台南': '台南市',
    '台南市': '台南市',
    '高雄': '高雄市',
    '高雄市': '高雄市',
    '基隆': '基隆市',
    '基隆市': '基隆市',
    '新竹': '新竹市',
    '新竹市': '新竹市',
    '嘉義': '嘉義市',
    '嘉義市': '嘉義市'
}

DISTRICT_CITY_MAP = {
    '中壢': '桃園市',
    '大園': '桃園市',
    '蘆竹': '桃園市',
    '八德': '桃園市',
    '平鎮': '桃園市',
    '龍潭': '桃園市',
    '龜山': '桃園市',
    '觀音': '桃園市',
    '新屋': '桃園市',
    '大溪': '桃園市',
    '楊梅': '桃園市',
    '復興': '桃園市',
    '中壢區': '桃園市',
    '大園區': '桃園市',
    '蘆竹區': '桃園市',
    '八德區': '桃園市',
    '平鎮區': '桃園市',
    '龍潭區': '桃園市',
    '龜山區': '桃園市',
    '觀音區': '桃園市',
    '新屋區': '桃園市',
    '大溪區': '桃園市',
    '楊梅區': '桃園市',
    '復興區': '桃園市',
    '林口區': '新北市'
}

AREA_PATTERN = re.compile(r'(\d+)\s*坪')
DISTRICT_PATTERN = re.compile(r'(?=([\u4e00-\u9fff]{2,5}(?:區|鄉|鎮|里)))')
ROAD_PATTERN = re.compile(r'([\u4e00-\u9fff0-9]{2,12}(?:路|街|大道)(?:一段|二段|三段|四段|五段|六段|七段|八段|九段|十段)?)')
NOISE_TERMS = ['實價登錄', '成交資料', '成交紀錄', '成交記錄', '成交', '查詢', '請問', '幫我查', '附近', '周邊']


def normalize_query(text):
    if not text:
        return ''
    text = text.replace('\u3000', ' ')
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_search_keyword(query):
    if not query:
        return None
    cleaned = query
    for alias in CITY_ALIASES:
        cleaned = cleaned.replace(alias, ' ')
    for district in DISTRICT_CITY_MAP:
        cleaned = cleaned.replace(district, ' ')
    for label in PERIOD_PATTERNS:
        cleaned = cleaned.replace(label, ' ')
    for term in NOISE_TERMS:
        cleaned = cleaned.replace(term, ' ')
    for _, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            cleaned = cleaned.replace(kw, ' ')
    # Remove section / subsection tokens so only free-form keywords remain
    section_terms = re.findall(r'[\u4e00-\u9fff0-9]{1,10}(?:段|小段)', cleaned)
    for sec in section_terms:
        cleaned = cleaned.replace(sec, ' ')
    cleaned = re.sub(r'[\s,，。；;：:]+', ' ', cleaned).strip()
    if not cleaned:
        return None
    candidate = cleaned.split()[-1]
    if len(candidate) >= 2:
        return candidate
    return None


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def subtract_months(base_date, months):
    return base_date - timedelta(days=months * 30)


def find_city(query, cities):
    query_for_city = query
    for road in find_roads(query_for_city):
        query_for_city = query_for_city.replace(road, ' ')
    matches = []
    for alias, canonical in CITY_ALIASES.items():
        if alias in query_for_city:
            matches.append((len(alias), canonical))
    for district, canonical_city in DISTRICT_CITY_MAP.items():
        if district in query_for_city:
            matches.append((len(district), canonical_city))
    for city in cities:
        if city in query_for_city:
            matches.append((len(city), city))
    if not matches:
        return None
    return max(matches)[1]


def find_category(query):
    for category, keywords in CATEGORY_KEYWORDS:
        for keyword in keywords:
            if keyword in query:
                return category, keyword
    return None, None


def find_period(query):
    today = date.today()
    for label, months in PERIOD_PATTERNS.items():
        if label in query:
            if months == 'year':
                return date(today.year, 1, 1).isoformat()
            return subtract_months(today, months).isoformat()
    custom = re.search(r'(\d+)\s*個月', query)
    if custom:
        return subtract_months(today, int(custom.group(1))).isoformat()
    custom_year = re.search(r'(\d+)\s*年', query)
    if custom_year:
        return subtract_months(today, int(custom_year.group(1)) * 12).isoformat()
    return None


def find_area(query):
    match = AREA_PATTERN.search(query)
    if match:
        return float(match.group(1))
    return None


def find_district(query, city=None):
    stripped = query
    for road in find_roads(query):
        stripped = stripped.replace(road, ' ')
    matches = DISTRICT_PATTERN.findall(stripped)
    if matches:
        return matches[-1]
    tokens = re.split(r'[\s,，。.]+', stripped)
    excluded = set([city or '']) | set(sum([keywords for _, keywords in CATEGORY_KEYWORDS], []))
    excluded |= set(CITY_ALIASES.keys())
    excluded |= {k for k in PERIOD_PATTERNS}
    if city:
        excluded.add(city)
    for token in tokens:
        token = token.strip()
        if not token or token in excluded:
            continue
        if token in DISTRICT_CITY_MAP:
            return token
    for district in sorted(DISTRICT_CITY_MAP.keys(), key=len, reverse=True):
        if district in stripped:
            return district
    return None


def find_roads(query):
    roads = []
    for m in ROAD_PATTERN.findall(query):
        road = m
        changed = True
        while changed:
            changed = False
            for prefix in sorted(list(CITY_ALIASES.keys()) + list(DISTRICT_CITY_MAP.keys()), key=len, reverse=True):
                if road.startswith(prefix) and len(road) > len(prefix) + 1:
                    road = road[len(prefix):]
                    changed = True
                    break
        if len(road) >= 2:
            roads.append(road)
    return roads


def find_section_name(query, city=None, district=None):
    cleaned = query
    for road in find_roads(cleaned):
        cleaned = cleaned.replace(road, ' ')
    if city:
        cleaned = cleaned.replace(city, ' ')
    for alias in CITY_ALIASES:
        if alias in cleaned:
            cleaned = cleaned.replace(alias, ' ')
    if district:
        cleaned = cleaned.replace(district, ' ')
    for dist in DISTRICT_CITY_MAP:
        if dist in cleaned:
            cleaned = cleaned.replace(dist, ' ')
    matches = re.findall(r'[\u4e00-\u9fff0-9]{1,10}(?:段|小段)', cleaned)
    if not matches:
        return None
    section_name = matches[-1]
    for alias in sorted(CITY_ALIASES.keys(), key=len, reverse=True):
        if section_name.startswith(alias) and len(section_name) > len(alias):
            section_name = section_name[len(alias):].strip()
            break
    return section_name


def parse_natural_query(text, config):
    if not text:
        return {}, ''
    query = normalize_query(text)
    params = {}
    if '租賃' not in query and '租金' not in query and '租屋' not in query:
        params['exclude_rent'] = True
    debug = {'input': text, 'normalized': query, 'steps': []}

    category, category_keyword = find_category(query)
    if category:
        params['category'] = category
        debug['steps'].append(f'category={category}')
        if category_keyword:
            query = query.replace(category_keyword, ' ')

    city = find_city(query, config.get('allowed_cities', []))
    if city:
        params['city'] = city
        debug['steps'].append(f'city={city}')

    roads = find_roads(query)
    if roads:
        params['road'] = roads[-1]
        debug['steps'].append(f'road={roads[-1]}')

    district = find_district(query, city)
    if district:
        params['district'] = district
        debug['steps'].append(f'district={district}')
        if not city and district in DISTRICT_CITY_MAP:
            params['city'] = DISTRICT_CITY_MAP[district]
            debug['steps'].append(f'inferred_city={params["city"]}')

    section_name = find_section_name(query, params.get('city'), params.get('district'))
    if section_name:
        params['section_name'] = section_name
        debug['steps'].append(f'section_name={section_name}')

    start_date = find_period(query)
    if start_date:
        params['start_date'] = start_date
        debug['steps'].append(f'start_date={start_date}')

    min_area_ping = find_area(query)
    if min_area_ping is not None:
        params['min_area_ping'] = min_area_ping
        debug['steps'].append(f'min_area_ping={min_area_ping}')

    if 'section_name' not in params and 'road' not in params:
        keyword = extract_search_keyword(query)
        if keyword:
            params['keyword'] = keyword
            debug['steps'].append(f'keyword={keyword}')

    params['_debug'] = debug

    return params, query


def build_category_clause(category):
    if not category:
        return '', []
    values = []
    if category == '車位':
        clause = '(transaction_target LIKE ? OR note LIKE ? OR location_raw LIKE ?)'
        values = ['%車位%', '%車位%', '%車位%']
    elif category == '建地':
        terms = ['住', '商', '工', '建地', '住宅區', '商業區', '工業區']
        clause_items = []
        for term in terms:
            clause_items.append('land_use_zone LIKE ?')
            clause_items.append('land_use_type LIKE ?')
            clause_items.append('note LIKE ?')
            values.extend([f'%{term}%'] * 3)
        clause = ('(transaction_target LIKE ? '
                  'AND transaction_target NOT LIKE ? '
                  'AND transaction_target NOT LIKE ? '
                  'AND transaction_target NOT LIKE ? '
                  'AND (' + ' OR '.join(clause_items) + '))')
        values = ['%土地%', '%建物%', '%房地%', '%車位%'] + values
    elif category == '農地':
        clause = '(land_use_zone LIKE ? OR land_use_type LIKE ? OR note LIKE ? OR transaction_target LIKE ?)'
        values = ['%農%', '%農%', '%農地%', '%農地%']
    elif category == '工業':
        clause = '(land_use_zone LIKE ? OR land_use_type LIKE ? OR note LIKE ? OR transaction_target LIKE ?)'
        values = ['%工業%', '%工業%', '%工業%', '%工%']
    elif category == '房屋':
        clause = '(transaction_target LIKE ? OR land_use_zone LIKE ? OR note LIKE ? OR location_raw LIKE ?)'
        values = ['%建物%', '%住%', '%房屋%', '%房地%']
    elif category == '土地':
        clause = '(transaction_target LIKE ? OR land_use_zone LIKE ? OR land_use_type LIKE ? OR note LIKE ? OR location_raw LIKE ?)'
        values = ['%土地%', '%地%', '%地%', '%土地%', '%土地%']
    elif category == '特殊':
        special_terms = [
            '贈與', '繼承', '交換', '親友', '特殊關係', '異常',
            '協議價購', '政府機關標讓售', '容積代金基金採購', '未登記建物'
        ]
        clause_items = []
        for term in special_terms:
            clause_items.append('note LIKE ?')
            values.append(f'%{term}%')
        clause = '(' + ' OR '.join(clause_items) + ')'
    else:
        clause = ''
    return clause, values


def build_query(params):
    sql_parts = [
        'SELECT DISTINCT lt.city, lt.district, lt.section_name, lt.land_number, lt.location_raw, lt.trade_date, lt.area_ping, lt.unit_price_per_ping_wan, lt.total_price_wan, lt.land_use_zone, NULLIF(TRIM(COALESCE(lt.note,\'\')),\'\'), lt.transaction_target, NULLIF(lt.building_area_sqm, 0.0)',
        'FROM land_transactions lt'
        ' WHERE 1=1'
    ]
    values = []

    if params.get('city'):
        sql_parts.append('AND lt.city = ?')
        values.append(params['city'])

    if params.get('exclude_rent'):
        sql_parts.append("AND COALESCE(lt.transaction_target, '') NOT LIKE ?")
        values.append('%租賃%')

    # 查詢優先順序：地段 → 路名 → 行政區。
    # 若只輸入「大園路」這類路名，不會因「大園」而誤套大園區。
    if params.get('district'):
        sql_parts.append('AND lt.district LIKE ?')
        values.append(f'%{params["district"]}%')

    if params.get('section_name'):
        sn = params['section_name']
        like = f'%{sn}%'
        sql_parts.append(
            'AND (lt.section_name LIKE ? OR lt.location_raw LIKE ? OR lt.raw_json LIKE ? '
            'OR EXISTS (SELECT 1 FROM land_details ld WHERE ld.record_id = lt.record_id '
            'AND (ld.location_raw LIKE ? OR ld.raw_json LIKE ?)))'
        )
        values.extend([like] * 5)

    if params.get('road') and not params.get('section_name'):
        road = params['road']
        like = f'%{road}%'
        sql_parts.append(
            'AND (lt.location_raw LIKE ? OR lt.raw_json LIKE ? '
            'OR EXISTS (SELECT 1 FROM land_details ld WHERE ld.record_id = lt.record_id '
            'AND (ld.location_raw LIKE ? OR ld.raw_json LIKE ?)))'
        )
        values.extend([like] * 4)

    if params.get('keyword'):
        kw = f'%{params["keyword"]}%'
        sql_parts.append(
            'AND ('
            'lt.section_name LIKE ? OR lt.location_raw LIKE ? OR lt.raw_json LIKE ? OR lt.transaction_target LIKE ? OR lt.note LIKE ? '
            'OR EXISTS (SELECT 1 FROM land_details ld WHERE ld.record_id = lt.record_id '
            'AND (ld.location_raw LIKE ? OR ld.raw_json LIKE ?))'
            ')'
        )
        values.extend([kw] * 7)

    if params.get('start_date'):
        sql_parts.append('AND lt.trade_date >= ?')
        values.append(params['start_date'])

    if params.get('min_area_ping') is not None:
        sql_parts.append('AND area_ping >= ?')
        values.append(params['min_area_ping'])
    else:
        # 過濾坪數過小（<5坪）的異常交易，除非明確查詢車位
        if params.get('category') != '車位':
            sql_parts.append('AND (lt.area_ping IS NULL OR lt.area_ping >= 5)')

    # 過濾持分、車位殘值等非正常土地交易（排行與統計不含異常資料）
    if not params.get('include_abnormal'):
        sql_parts.append(
            "AND COALESCE(lt.note, '') NOT LIKE '%持分%'"
        )
        sql_parts.append(
            "AND COALESCE(lt.note, '') NOT LIKE '%殘值%'"
        )
        if params.get('category') != '車位':
            sql_parts.append(
                "AND COALESCE(lt.note, '') NOT LIKE '%車位%'"
            )
            sql_parts.append(
                "AND COALESCE(lt.transaction_target, '') NOT LIKE '%車位%'"
            )

    category_clause, category_values = build_category_clause(params.get('category'))
    if category_clause:
        sql_parts.append('AND ' + category_clause)
        values.extend(category_values)

    sql_parts.append('ORDER BY lt.trade_date DESC LIMIT 100')
    return ' '.join(sql_parts), values


def format_row(row):
    city, district, section_name, land_number, location_raw, trade_date, area_ping, unit_price_per_ping_wan, total_price_wan, land_use_zone, note, transaction_target, building_area_sqm = row
    return {
        'city': city,
        'district': district,
        'section_name': section_name,  # 保留原始值（含 PUA 字元），不自動替換
        'land_number': land_number,
        'location_raw': location_raw,  # 保留原始值
        'trade_date': trade_date,
        'area_ping': area_ping,
        'unit_price_per_ping_wan': unit_price_per_ping_wan,
        'total_price_wan': total_price_wan,
        'land_use_zone': land_use_zone,
        'note': note,
        'transaction_target': transaction_target,
        'building_area_sqm': building_area_sqm
    }


def is_house_transaction(row):
    target = (row.get('transaction_target') or '')
    return '房地' in target or '建物' in target


def has_parking(row):
    target = (row.get('transaction_target') or '')
    note = (row.get('note') or '')
    return '車位' in target or '車位' in note


def build_display_metrics(row):
    total_price = row.get('total_price_wan')
    building_area_sqm = row.get('building_area_sqm')
    land_area_ping = row.get('area_ping')
    if is_house_transaction(row) and building_area_sqm and building_area_sqm > 0:
        area_ping = building_area_sqm / 3.305785
        unit_price = total_price / area_ping if total_price and area_ping else None
        return {
            'area_label': '建物坪數',
            'area_ping': area_ping,
            'price_label': '建物單價',
            'unit_price': unit_price,
            'warning': '⚠️ 含車位，單價僅供參考' if has_parking(row) else ''
        }
    area_ping = land_area_ping
    unit_price = total_price / area_ping if total_price and area_ping else row.get('unit_price_per_ping_wan')
    return {
        'area_label': '土地坪數',
        'area_ping': area_ping,
        'price_label': '土地單價',
        'unit_price': unit_price,
        'warning': ''
    }


def print_results(results, params):
    if not results:
        print('No records found for query conditions.')
        if params:
            print('Query conditions:', params)
        return
    print(f'共 {len(results)} 筆')
    print()
    for row in results:
        print(_format_card(row))


def _format_card(row):
    metrics = build_display_metrics(row)
    place = f'{row["city"]}{row["district"] or ""}' if row.get("district") else row["city"]
    section = row.get('section_name') or ''
    land_num = row.get('land_number') or ''
    loc_raw = row.get('location_raw') or ''
    trade_date = row.get('trade_date') or ''
    land_use_zone = row.get('land_use_zone') or ''
    total = row.get('total_price_wan')

    is_address = '地號' not in loc_raw and any(k in loc_raw for k in ('路', '街', '巷', '弄', '號'))

    # Extract land number from location_raw if land_number column is empty
    if not land_num and '地號' in loc_raw:
        m = re.search(r'(\d[\d\-\/]*)\s*地號', loc_raw)
        if m:
            land_num = m.group(1)

    section_disp = re.sub(r'[-]', '？', section)
    if section_disp and land_num:
        loc_part = f'{section_disp}{land_num}地號'
    elif section_disp:
        loc_part = section_disp
    elif not is_address and loc_raw:
        loc_part = loc_raw
    else:
        loc_part = ''

    lines = [f'📍 {place}｜{loc_part}' if loc_part else f'📍 {place}']

    if is_address and loc_raw:
        lines.append(f'🏠 {loc_raw}')

    txn = row.get('transaction_target') or ''
    zone_str = f'（{land_use_zone}）' if land_use_zone else ''
    lines.append(f'📅 {trade_date}｜{txn}{zone_str}')

    area_val = f'{metrics["area_ping"]:.2f}坪' if metrics.get('area_ping') else 'N/A'
    unit_val = f'{metrics["unit_price"]:.2f}萬/坪' if metrics.get('unit_price') else 'N/A'
    total_val = f'{total:.0f}萬' if total else 'N/A'
    area_label = '建物' if metrics.get('area_label') == '建物坪數' else '土地'
    lines.append(f'📐 {area_label}{area_val}｜💵 {unit_val}｜💰 {total_val}')

    if metrics.get('warning'):
        lines.append(metrics['warning'])

    lines.append('━━━━━━━━━━')
    return '\n'.join(lines)


def detect_search_level(params: dict) -> str:
    """判斷查詢層級：city / district / section / subsection / land_number"""
    q = params.get('section_name', '') or params.get('keyword', '')
    if '小段' in q:
        return 'subsection'
    if '地號' in q or re.search(r'\d+地號', q):
        return 'land_number'
    if params.get('section_name'):
        return 'section'
    if params.get('district'):
        return 'district'
    if params.get('city'):
        return 'city'
    return 'keyword'


def ranking_label(params: dict) -> str:
    """產生排行標題用的地名標籤。"""
    parts = []
    if params.get('city'):
        parts.append(params['city'])
    if params.get('district'):
        parts.append(params['district'])
    if params.get('section_name'):
        parts.append(params['section_name'])
    elif params.get('keyword'):
        parts.append(params['keyword'])
    return '｜'.join(parts) if parts else '查詢結果'


def build_ranking(params: dict, db_path=None, top_n: int = 10) -> list[dict]:
    """
    依總價排行，回傳最近一年（預設）前 N 筆，由高到低。
    若 params 已有 start_date 則沿用；否則預設近一年。
    """
    p = dict(params)
    if 'start_date' not in p:
        one_year_ago = (date.today().replace(year=date.today().year - 1)).isoformat()
        p['start_date'] = one_year_ago

    sql, values = build_query(p)
    # 替換 ORDER BY 為總價排行
    sql = re.sub(r'ORDER BY .+$', f'ORDER BY lt.total_price_wan DESC NULLS LAST LIMIT {top_n}', sql)

    path = str(db_path or DB_PATH)
    with sqlite3.connect(path) as conn:
        rows = [format_row(r) for r in conn.execute(sql, values).fetchall()]
    return rows


def format_ranking(rows: list[dict], params: dict) -> str:
    """排行榜文字格式，供 Telegram 和 CLI 共用。"""
    if not rows:
        return f'🔍 {ranking_label(params)}\n\n查無近一年成交資料。'

    medals = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    label = ranking_label(params)
    lines = [f'🏆 {label}｜成交總價排行（近一年）', '']

    for i, row in enumerate(rows):
        medal = medals[i] if i < len(medals) else f'{i+1}.'
        section = row.get('section_name') or ''
        land_num = row.get('land_number') or ''
        loc_raw = row.get('location_raw') or ''

        if not land_num and '地號' in loc_raw:
            m = re.search(r'(\d[\d\-\/]*)\s*地號', loc_raw)
            if m:
                land_num = m.group(1)

        section_disp = re.sub(r'[-]', '？', section)
        loc_part = f'{section_disp}{land_num}地號' if section_disp and land_num else (section_disp or loc_raw[:20])

        trade_date = row.get('trade_date') or ''
        total = row.get('total_price_wan')
        total_str = f'{total:.0f}萬' if total else 'N/A'

        metrics = build_display_metrics(row)
        unit_str = f'{metrics["unit_price"]:.0f}萬/坪' if metrics.get('unit_price') else 'N/A'
        area_str = f'{metrics["area_ping"]:.1f}坪' if metrics.get('area_ping') else 'N/A'

        zone = row.get('land_use_zone') or ''
        zone_part = f'｜{zone}' if zone else ''
        lines.append(f'{medal} {trade_date}｜{loc_part}{zone_part}')
        lines.append(f'💰 {total_str}｜{unit_str}｜{area_str}')
        lines.append('')

    return '\n'.join(lines).rstrip()


def summarize_results(rows: list[dict]) -> dict:
    units = []
    dates = []
    for row in rows:
        metrics = build_display_metrics(row)
        unit = metrics.get('unit_price')
        if unit:
            units.append(unit)
        if row.get('trade_date'):
            dates.append(row['trade_date'])
    return {
        'count': len(rows),
        'avg_unit': sum(units) / len(units) if units else None,
        'max_unit': max(units) if units else None,
        'min_unit': min(units) if units else None,
        'latest_date': max(dates) if dates else None,
    }


def format_summary(summary: dict) -> str:
    def money(v):
        return f'{v:.2f}萬/坪' if v is not None else 'N/A'
    return '\n'.join([
        f'成交筆數：{summary["count"]}',
        f'平均單價：{money(summary.get("avg_unit"))}',
        f'最高單價：{money(summary.get("max_unit"))}',
        f'最低單價：{money(summary.get("min_unit"))}',
        f'最新成交日期：{summary.get("latest_date") or "N/A"}',
    ])


def build_summary_query(params: dict) -> tuple[str, list]:
    detail_sql, values = build_query(params)
    _, from_where = detail_sql.split('FROM land_transactions lt', 1)
    from_where = re.sub(r'\s+ORDER BY lt\.trade_date DESC LIMIT 100\s*$', '', from_where)
    unit_expr = """
        CASE
            WHEN (COALESCE(lt.transaction_target, '') LIKE '%房地%'
                  OR COALESCE(lt.transaction_target, '') LIKE '%建物%')
                 AND lt.building_area_sqm IS NOT NULL
                 AND lt.building_area_sqm > 0
                 AND lt.total_price_wan IS NOT NULL
            THEN lt.total_price_wan / (lt.building_area_sqm / 3.305785)
            ELSE COALESCE(lt.total_price_wan / NULLIF(lt.area_ping, 0), lt.unit_price_per_ping_wan)
        END
    """
    sql = f"""
        SELECT
            COUNT(DISTINCT lt.id),
            AVG({unit_expr}),
            MAX({unit_expr}),
            MIN({unit_expr}),
            MAX(lt.trade_date)
        FROM land_transactions lt
        {from_where}
    """
    return ' '.join(sql.split()), values


def summarize_query(params: dict, db_path=None) -> dict:
    path = str(db_path or DB_PATH)
    sql, values = build_summary_query(params)
    with sqlite3.connect(path) as conn:
        count, avg_unit, max_unit, min_unit, latest_date = conn.execute(sql, values).fetchone()
    return {
        'count': count or 0,
        'avg_unit': avg_unit,
        'max_unit': max_unit,
        'min_unit': min_unit,
        'latest_date': latest_date,
    }


def suggest_similar(params: dict, db_path=None, limit: int = 5) -> list[str]:
    path = str(db_path or DB_PATH)
    suggestions = []
    keyword = params.get('road') or params.get('section_name') or params.get('keyword') or ''
    if not keyword:
        district = params.get('district')
        if district:
            keyword = district.replace('區', '')
    if not keyword:
        return suggestions

    search_terms = [keyword]
    if params.get('road'):
        road_base = re.sub(r'(路|街|大道)$', '', params['road'])
        if len(road_base) >= 2:
            search_terms.append(road_base)
        if len(road_base) >= 3:
            search_terms.append(road_base[:2])
    elif params.get('district'):
        search_terms.append(params['district'].replace('區', ''))

    sql = """
        SELECT DISTINCT city, district, section_name, location_raw
        FROM land_transactions
        WHERE section_name LIKE ? OR location_raw LIKE ? OR raw_json LIKE ?
        ORDER BY trade_date DESC
        LIMIT 80
    """
    seen = set()
    with sqlite3.connect(path) as conn:
        for term in search_terms:
            like = f'%{term}%'
            rows = conn.execute(sql, (like, like, like)).fetchall()
            for city, district, section, loc in rows:
                roads = find_roads(loc or '')
                if roads:
                    label = f'{city or ""}{district or ""}{roads[-1]}'
                elif section:
                    label = f'{city or ""}{district or ""}{section}'
                elif district:
                    label = f'{city or ""}{district}'
                else:
                    label = f'{city or ""}{district or ""}{term}'
                if label and label not in seen:
                    seen.add(label)
                    suggestions.append(label)
                if len(suggestions) >= limit:
                    return suggestions
    return suggestions


def log_query_debug(user_input: str, params: dict, sql: str, values: list, hit_count: int, db_path=None) -> None:
    log_path = PROJECT_ROOT / 'logs' / 'query_parser_debug.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    safe_params = {k: v for k, v in params.items() if not k.startswith('_')}
    payload = {
        'time': datetime.now().isoformat(timespec='seconds'),
        'input': user_input,
        'parsed': safe_params,
        'parser_steps': params.get('_debug', {}).get('steps', []),
        'sql': sql,
        'values': values,
        'hit_count': hit_count,
    }
    with log_path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description='自然語言查詢土地成交資料')
    parser.add_argument('query', nargs='+', help='例如：泰山 建地 近四個月成交')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite 資料庫路徑')
    parser.add_argument('--rank', action='store_true', help='顯示成交總價排行（前10筆）')
    args = parser.parse_args()

    config = load_config()
    params, query_text = parse_natural_query(' '.join(args.query), config)
    if 'city' not in params and 'district' not in params and 'section_name' not in params and 'road' not in params and 'keyword' not in params:
        print('請在查詢字串中指定縣市、行政區、地段或關鍵地名，例如：桃園市、新林段、三塊石。')
        return

    if args.rank:
        rows = build_ranking(params, db_path=args.db)
        print(format_ranking(rows, params))
        return

    sql, values = build_query(params)
    with sqlite3.connect(args.db) as conn:
        cursor = conn.execute(sql, values)
        rows = [format_row(row) for row in cursor.fetchall()]

    print(f'Query: {query_text}')
    print(f'Params: {params}')
    print_results(rows, params)


if __name__ == '__main__':
    main()
