#!/usr/bin/env python3
"""
parse_realprice.py  ── v2

設計鐵則（依 ENGINEERING_CORE.md / PROJECT_RULES.md / 老蕭LAND v4 PDF）：

    1. 不再過濾任何交易資料
    2. 房地、土地、車位、工業、特殊交易全部保留
    3. 原始 CSV 欄位完整保留（raw_json + original_csv_row）
    4. SQLite 不可刪除原始資料
    5. query 階段才做篩選
    6. 不做 AI 判斷
    7. 不做 worth_score
    8. 不做謄本判斷
    9. 不啟用 LLM
   10. allow_llm = False

支援檔案：
    [code]_lvr_land_a.csv         主檔 不動產買賣  → source_kind='sale',    source_file_type='main'
    [code]_lvr_land_a_land.csv    副檔 土地明細    → source_kind='sale',    source_file_type='land_detail'
    [code]_lvr_land_a_build.csv   副檔 建物明細    → source_kind='sale',    source_file_type='build_detail'
    [code]_lvr_land_a_park.csv    副檔 車位明細    → source_kind='sale',    source_file_type='park_detail'
    [code]_lvr_land_b.csv         主檔 預售屋買賣  → source_kind='presale', source_file_type='main'
    [code]_lvr_land_b_land.csv    副檔 預售土地    → source_kind='presale', source_file_type='land_detail'
    [code]_lvr_land_b_park.csv    副檔 預售車位    → source_kind='presale', source_file_type='park_detail'
    [code]_lvr_land_c.csv         主檔 不動產租賃  → source_kind='rent',    source_file_type='main'
    [code]_lvr_land_c_build.csv   副檔 租賃建物    → source_kind='rent',    source_file_type='build_detail'
    [code]_lvr_land_c_land.csv    副檔 租賃土地    → source_kind='rent',    source_file_type='land_detail'
    [code]_lvr_land_c_park.csv    副檔 租賃車位    → source_kind='rent',    source_file_type='park_detail'

入庫表：
    主檔 → land_transactions
    _land 副檔 → land_details
    _build 副檔 → build_details
    _park 副檔 → park_details
"""

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'config' / 'realprice_config.json'
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'

# ------------------- 安全護欄（v4 規則）-------------------
ALLOW_LLM = False
ALLOW_WORTH_SCORE = False
ALLOW_TITLEDEED_JUDGEMENT = False
FILTER_AT_PARSE = False
# ----------------------------------------------------------

# ------------------- PUA 字元映射 ---------------------------
# 內政部實價登錄 CSV 部分 CNS11643 補充字元以 PUA 碼（U+E000~U+F8FF）儲存。
# 僅對衍生欄（section_name）做正規化顯示；location_raw / raw_json 保持原始值。
PUA_CHAR_MAP: dict[str, str] = {
    '': '菓',  # 桃園市大園區菓林段，CNS11643補充字元
}


def has_pua_chars(text: str | None) -> bool:
    """是否含 PUA 碼點（U+E000–U+F8FF）。"""
    if not text:
        return False
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def normalize_pua(text: str | None) -> str | None:
    """將 PUA 字元替換為已知對應的標準 Unicode（僅用於衍生欄，不修改 location_raw）。"""
    if not text:
        return text
    for pua, correct in PUA_CHAR_MAP.items():
        text = text.replace(pua, correct)
    return text


def has_pua_chars(text: str | None) -> bool:
    """檢查字串是否含有 PUA 碼點（U+E000–U+F8FF）。"""
    if not text:
        return False
    return any(0xE000 <= ord(c) <= 0xF8FF for c in text)


def normalize_pua(text: str | None) -> str | None:
    """將已知 PUA 字元替換為標準 Unicode。"""
    if not text:
        return text
    for pua, correct in PUA_CHAR_MAP.items():
        text = text.replace(pua, correct)
    return text


# =========================================================
# 基礎工具
# =========================================================
def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    # 雙重保險：若有人改 config 想啟用 LLM，這裡強制蓋回 False
    cfg.setdefault('engine', {})
    cfg['engine']['allow_llm'] = False
    cfg['engine']['allow_worth_score'] = False
    cfg['engine']['allow_titledeed_judgement'] = False
    cfg['engine']['filter_at_parse'] = False
    return cfg


def get_csv_dir(cfg):
    p = cfg['files']['csv_dir']
    if p.startswith('../'):
        return PROJECT_ROOT / p[3:]
    return Path(p)


def normalize_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(',', '').replace('，', '')
    if text in ('', '-', '－'):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(value):
    """支援民國 YYY MM DD（7 位數）+ 標準 ISO/西元格式。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # ROC year: 1150409 = 民國 115 年 4 月 9 日 = 2026-04-09
    if len(text) == 7 and text.isdigit():
        try:
            roc_year = int(text[:3])
            month = int(text[3:5])
            day = int(text[5:7])
            return f"{roc_year + 1911:04d}-{month:02d}-{day:02d}"
        except ValueError:
            pass
    if len(text) == 6 and text.isdigit():
        # 民國 YYY MM（月份）
        try:
            roc_year = int(text[:3])
            month = int(text[3:5])
            return f"{roc_year + 1911:04d}-{month:02d}-01"
        except ValueError:
            pass

    for fmt in ('%Y/%m/%d', '%Y-%m-%d', '%Y/%m', '%Y-%m'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def to_ping(area_sqm):
    if area_sqm is None or area_sqm <= 0:
        return None
    return round(area_sqm / 3.305785, 4)


# =========================================================
# 檔名解析：縣市代碼 + source_kind + source_file_type
# =========================================================
FNAME_RE = re.compile(
    r'^(?P<code>[a-z])_lvr_land_(?P<kind>[abc])'
    r'(?:_(?P<sub>land|build|park))?\.csv$',
    re.IGNORECASE,
)


def classify_file(fname, city_code_map):
    m = FNAME_RE.match(fname.lower())
    if not m:
        return None
    code = m.group('code').lower()
    kind = m.group('kind').lower()      # a / b / c
    sub = m.group('sub')                # None / land / build / park

    city = city_code_map.get(code)
    source_kind = {'a': 'sale', 'b': 'presale', 'c': 'rent'}[kind]
    source_file_type = {
        None: 'main',
        'land': 'land_detail',
        'build': 'build_detail',
        'park': 'park_detail',
    }[sub]
    return {
        'city': city,
        'code': code,
        'source_kind': source_kind,
        'source_file_type': source_file_type,
    }


# =========================================================
# 地段、地號擷取
# =========================================================
def extract_section_and_number(location_raw, debug=False):
    """從「土地位置建物門牌」擷取地段名稱與地號。

    設計原則：
    - 「地號」是土地位置的核心識別碼；只有含「地號」才嘗試解析段名
    - 即使同一欄位也含門牌路名，只要有「地號」就嘗試提取段名（兩者可共存）
    - 候選段名以路／街／大道結尾 → 路段號，非地段名，跳過
    - section_name 做 PUA 正規化；location_raw 由呼叫端自行保留原始值
    - debug=True 時印出解析過程
    """
    if not location_raw:
        return None, None

    section = None
    land_number = None

    if debug:
        print(f'[DEBUG extract] location_raw={repr(location_raw[:80])}')

    # 地號：取「地號」前的數字串（含連字號，如 76、122、151-1、1368-0000）
    n = re.search(r'(\d[\d\-\/]*)\s*地號', location_raw)
    if n:
        land_number = n.group(1).strip()

    # 段名：只在含「地號」時嘗試擷取（代表這是土地位置，不只是門牌地址）
    if '地號' in location_raw:
        m = re.search(
            r'(?:^|[^一-鿿\ue000-\uf8ff0-9０-９])([一-鿿\ue000-\uf8ff0-9０-９]{2,30}(?:小段|段))',
            location_raw,
        )
        if m:
            candidate = m.group(1).strip()
            if re.search(
                r'(?:路|街|大道|公路|快速道路|高速公路)(?:[一二三四五六七八九十百千]|\d+)?$',
                candidate,
            ):
                if debug:
                    print(f'[DEBUG extract] 路段誤判跳過: {repr(candidate)}')
            else:
                section = normalize_pua(candidate)
                if debug and has_pua_chars(candidate):
                    print(f'[DEBUG extract] PUA 正規化: {repr(candidate)} → {repr(section)}')

    if debug:
        print(f'[DEBUG extract] → section={repr(section)}  land_number={repr(land_number)}')

    return section, land_number
def classify_target_category(target, zone, source_kind, rules):
    """純規則式，不呼叫任何 LLM。"""
    target = str(target or '')
    zone = str(zone or '')
    for r in rules:
        if 'if_source_kind' in r and r['if_source_kind'] != source_kind:
            continue
        if 'if_target_contains' in r:
            if not any(t in target for t in r['if_target_contains']):
                continue
        if 'if_zone_contains' in r:
            if not any(z in zone for z in r['if_zone_contains']):
                continue
        return r['name']
    return '其他'


# =========================================================
# unique_key（去重；非過濾）
# =========================================================
def build_main_unique_key(rec):
    # 穩定鍵：僅用業務資料，不含 source_kind/file_type（避免格式升級造成重複匯入）
    # record_id 有值時附加，確保跨批次相同交易共用同一 unique_key
    base_parts = [
        rec.get('city') or '',
        rec.get('district') or '',
        rec.get('location_raw') or '',
        rec.get('trade_date') or '',
        str(rec.get('area_sqm') or ''),
        str(rec.get('total_price') or ''),
    ]
    rid = rec.get('record_id') or ''
    if rid:
        base_parts.append(rid)
    return '|'.join(base_parts)


def build_sub_unique_key(prefix, rec, fname):
    return '|'.join([
        prefix,
        rec.get('source_kind') or '',
        rec.get('record_id') or '',
        fname,
        rec.get('location_raw') or '',
        str(rec.get('area_sqm') or rec.get('park_area_sqm') or rec.get('building_area_sqm') or ''),
    ])


# =========================================================
# 解析單列：主檔（_a / _b / _c）
# =========================================================
def parse_main_row(row, file_info, cfg):
    """完全不過濾。任何欄位解析失敗都先 None，整列照存。"""
    warnings = []
    mf = cfg['main_source_fields']
    rf = cfg.get('rent_source_fields', {})

    # 租賃 schema 的「交易年月日」叫「租賃年月日」、「總價元」叫「總額元」
    trade_date_field = rf.get('trade_date', mf['trade_date']) \
        if file_info['source_kind'] == 'rent' else mf['trade_date']
    total_price_field = rf.get('total_price', mf['total_price']) \
        if file_info['source_kind'] == 'rent' else mf['total_price']
    area_field = rf.get('area_sqm', mf['area_sqm']) \
        if file_info['source_kind'] == 'rent' else mf['area_sqm']

    # 第一列 schema 範例（英文 header）跳過
    sample_value = (row.get(mf['district']) or '').strip()
    if sample_value in (
        'The villages and towns urban district',
        '鄉鎮市區', '',
    ) and not any(row.values()):
        return None

    district = (row.get(mf['district']) or '').strip() or None
    target = (row.get(mf['transaction_target']) or '').strip() or None
    location_raw = (row.get(mf['location_raw']) or '').strip() or None
    area_sqm = normalize_number(row.get(area_field))
    building_area_sqm = normalize_number(row.get(mf['building_area_sqm']))
    zone = (row.get(mf['land_use_zone']) or '').strip() or None
    use_type = (row.get(mf['land_use_type']) or '').strip() or None
    if not use_type:
        use_type = (row.get(mf['land_use_definition']) or '').strip() or None
    trade_date_iso = parse_date(row.get(trade_date_field))
    if row.get(trade_date_field) and trade_date_iso is None:
        warnings.append(f"trade_date_parse_fail:{row.get(trade_date_field)}")
    total_price = normalize_number(row.get(total_price_field))
    unit_price_per_sqm = normalize_number(row.get(mf['unit_price_per_sqm']))

    city = file_info.get('city')
    trimmed_location_raw = location_raw
    if location_raw and city:
        alt_city = city.replace('台', '臺') if '台' in city else city.replace('臺', '台')
        if trimmed_location_raw.startswith(city):
            trimmed_location_raw = trimmed_location_raw[len(city):].strip()
        elif trimmed_location_raw.startswith(alt_city):
            trimmed_location_raw = trimmed_location_raw[len(alt_city):].strip()
    if trimmed_location_raw and district:
        alt_district = district.replace('台', '臺') if '台' in district else district.replace('臺', '台')
        if trimmed_location_raw.startswith(district):
            trimmed_location_raw = trimmed_location_raw[len(district):].strip()
        elif trimmed_location_raw.startswith(alt_district):
            trimmed_location_raw = trimmed_location_raw[len(alt_district):].strip()
    section_name, land_number = extract_section_and_number(trimmed_location_raw)

    # 用 location_raw 補抓 city（若檔名沒給）
    if not city and location_raw:
        for c in cfg['city_code_map'].values():
            if location_raw.startswith(c) or location_raw.startswith(c.replace('台', '臺')):
                city = c
                break

    target_category = classify_target_category(
        target, zone or use_type, file_info['source_kind'], cfg['category_rules']
    )

    area_ping = to_ping(area_sqm)
    total_price_wan = round(total_price / 10000.0, 4) if total_price else None
    unit_price_per_ping_wan = None
    if area_ping and total_price:
        unit_price_per_ping_wan = round(total_price / 10000.0 / area_ping, 2)

    # 移轉層次 vs 移轉情形（_a 主檔用「移轉層次」、_b 預售用「移轉情形」）
    transaction_type = (
        (row.get(mf['transaction_type_a']) or '').strip()
        or (row.get(mf['transaction_type_b']) or '').strip()
        or None
    )

    parse_status = 'ok'
    if not trade_date_iso or not location_raw:
        parse_status = 'partial'
    if not any(row.values()):
        parse_status = 'unparseable'

    rec = {
        'source_kind':           file_info['source_kind'],
        'source_file_type':      file_info['source_file_type'],
        'target_category':       target_category,
        'city':                  city,
        'district':              district,
        'section_name':          section_name,
        'land_number':           land_number,
        'location_raw':          location_raw,
        'trade_date':            trade_date_iso,
        'area_sqm':              area_sqm,
        'area_ping':             area_ping,
        'building_area_sqm':     building_area_sqm,
        'total_price':           int(total_price) if total_price else None,
        'total_price_wan':       total_price_wan,
        'unit_price_per_sqm':    unit_price_per_sqm,
        'unit_price_per_ping_wan': unit_price_per_ping_wan,
        'land_use_zone':         zone,
        'land_use_type':         use_type,
        'building_type':         (row.get(mf['building_type']) or '').strip() or None,
        'main_use':              (row.get(mf['main_use']) or '').strip() or None,
        'main_material':         (row.get(mf['main_material']) or '').strip() or None,
        'transaction_type':      transaction_type,
        'transaction_target':    target,
        'note':                  (row.get(mf['note']) or '').strip() or None,
        'record_id':             (row.get(mf['record_id']) or '').strip() or None,
        'raw_json':              json.dumps(row, ensure_ascii=False),
        'parse_status':          parse_status,
        'parse_warnings':        ';'.join(warnings) if warnings else None,
    }
    return rec


# =========================================================
# 解析單列：副檔（_land / _build / _park）
# =========================================================
def parse_land_detail_row(row):
    return {
        'record_id':        (row.get('編號') or '').strip() or None,
        'location_raw':     (row.get('土地位置') or '').strip() or None,
        'area_sqm':         normalize_number(row.get('土地移轉面積平方公尺')),
        'zoning':           (row.get('使用分區或編定') or '').strip() or None,
        'share_num':        normalize_number(row.get('權利人持分分子')),
        'share_den':        normalize_number(row.get('權利人持分分母')),
        'transfer_status':  (row.get('移轉情形') or '').strip() or None,
        'land_number':      (row.get('地號') or '').strip() or None,
    }


def parse_build_detail_row(row):
    return {
        'record_id':         (row.get('編號') or '').strip() or None,
        'building_age':      (row.get('屋齡') or '').strip() or None,
        'building_area_sqm': normalize_number(row.get('建物移轉面積平方公尺')),
        'main_use':          (row.get('主要用途') or '').strip() or None,
        'main_material':     (row.get('主要建材') or '').strip() or None,
        'build_completion':  (row.get('建築完成日期') or '').strip() or None,
        'total_floors':      (row.get('總層數') or '').strip() or None,
        'building_floor':    (row.get('建物分層') or '').strip() or None,
        'transfer_status':   (row.get('移轉情形') or '').strip() or None,
    }


def parse_park_detail_row(row):
    return {
        'record_id':        (row.get('編號') or '').strip() or None,
        'park_type':        (row.get('車位類別') or '').strip() or None,
        'park_price':       normalize_number(row.get('車位價格')),
        'park_area_sqm':    normalize_number(row.get('車位面積平方公尺')),
        'park_floor':       (row.get('車位所在樓層') or '').strip() or None,
    }


# =========================================================
# Insert helpers
# =========================================================
MAIN_COLUMNS = [
    'source_kind', 'source_file_type', 'target_category',
    'city', 'district', 'section_name', 'land_number', 'location_raw',
    'trade_date', 'area_sqm', 'area_ping', 'building_area_sqm',
    'total_price', 'total_price_wan', 'unit_price_per_sqm', 'unit_price_per_ping_wan',
    'land_use_zone', 'land_use_type',
    'building_type', 'main_use', 'main_material',
    'transaction_type', 'transaction_target', 'note', 'record_id',
    'raw_json', 'original_csv_row',
    'source_file', 'parse_status', 'parse_warnings', 'unique_key',
]

LAND_COLUMNS = [
    'source_kind', 'record_id', 'city', 'location_raw', 'area_sqm', 'zoning',
    'share_num', 'share_den', 'transfer_status', 'land_number',
    'raw_json', 'original_csv_row', 'source_file', 'unique_key',
]

BUILD_COLUMNS = [
    'source_kind', 'record_id', 'building_age', 'building_area_sqm',
    'main_use', 'main_material', 'build_completion', 'total_floors',
    'building_floor', 'transfer_status',
    'raw_json', 'original_csv_row', 'source_file', 'unique_key',
]

PARK_COLUMNS = [
    'source_kind', 'record_id', 'park_type', 'park_price', 'park_area_sqm', 'park_floor',
    'raw_json', 'original_csv_row', 'source_file', 'unique_key',
]


def insert_with_columns(conn, table, columns, rec):
    # 對 land_transactions 主表：unique_key 衝突前先做業務鍵二次防重
    # 防止 record_id=NULL 版本與有 record_id 版本各自插入（unique_key 不同但同一筆交易）
    if table == 'land_transactions' and not rec.get('record_id'):
        # 只對 record_id=NULL 的資料做業務鍵防重，避免同一筆交易以不同 unique_key 重複插入
        # 若 record_id 有值，交由 unique_key UNIQUE 約束處理，不阻擋合法共有交易
        exists = conn.execute(
            'SELECT 1 FROM land_transactions WHERE location_raw IS ? AND trade_date IS ? AND area_sqm IS ? AND total_price IS ? LIMIT 1',
            (rec.get('location_raw'), rec.get('trade_date'), rec.get('area_sqm'), rec.get('total_price'))
        ).fetchone()
        if exists:
            return 0
    placeholders = ','.join(['?'] * len(columns))
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    values = [rec.get(c) for c in columns]
    return conn.execute(sql, values).rowcount


# =========================================================
# 主流程
# =========================================================
def parse_csv_file(path, file_info, cfg):
    """讀整檔，全部入庫，回傳 (records_for_table, stats)。"""
    stats = {'total': 0, 'inserted': 0, 'skipped_dup': 0, 'parse_partial': 0, 'parse_failed': 0}
    out = []

    sk = file_info['source_kind']
    ft = file_info['source_file_type']

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        for raw_row in reader:
            stats['total'] += 1

            # 第一列常見英文 header sample（如 The villages and towns urban district），跳過
            if any(str(v).startswith('The ') for v in raw_row.values() if v):
                continue

            original_csv_row = ','.join(
                (raw_row.get(fn) or '') for fn in fieldnames
            )

            if ft == 'main':
                rec = parse_main_row(raw_row, file_info, cfg)
                if rec is None:
                    continue
                rec['source_file'] = path.name
                rec['original_csv_row'] = original_csv_row
                rec['unique_key'] = build_main_unique_key(rec)
                if rec.get('parse_status') == 'partial':
                    stats['parse_partial'] += 1
                if rec.get('parse_status') == 'unparseable':
                    stats['parse_failed'] += 1
                out.append(('land_transactions', MAIN_COLUMNS, rec))

            elif ft == 'land_detail':
                rec = parse_land_detail_row(raw_row)
                rec['source_kind'] = sk
                rec['city'] = file_info.get('city')
                rec['raw_json'] = json.dumps(raw_row, ensure_ascii=False)
                rec['original_csv_row'] = original_csv_row
                rec['source_file'] = path.name
                rec['unique_key'] = build_sub_unique_key('LD', rec, path.name)
                out.append(('land_details', LAND_COLUMNS, rec))

            elif ft == 'build_detail':
                rec = parse_build_detail_row(raw_row)
                rec['source_kind'] = sk
                rec['raw_json'] = json.dumps(raw_row, ensure_ascii=False)
                rec['original_csv_row'] = original_csv_row
                rec['source_file'] = path.name
                rec['unique_key'] = build_sub_unique_key('BD', rec, path.name)
                out.append(('build_details', BUILD_COLUMNS, rec))

            elif ft == 'park_detail':
                rec = parse_park_detail_row(raw_row)
                rec['source_kind'] = sk
                rec['raw_json'] = json.dumps(raw_row, ensure_ascii=False)
                rec['original_csv_row'] = original_csv_row
                rec['source_file'] = path.name
                rec['unique_key'] = build_sub_unique_key('PD', rec, path.name)
                out.append(('park_details', PARK_COLUMNS, rec))

    return out, stats


def import_csv_files(db_path=None, csv_dir=None, verbose=True):
    if FILTER_AT_PARSE or ALLOW_LLM:
        raise RuntimeError("v4 鐵則：parse 階段禁止過濾與 LLM。")

    cfg = load_config()
    db_path = Path(db_path or DB_PATH)
    csv_dir = Path(csv_dir) if csv_dir else get_csv_dir(cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    total_added = total_skipped = total_partial = total_failed = total_rows = 0
    per_file = []

    with sqlite3.connect(db_path) as conn:
        # 確保 schema 存在（CREATE IF NOT EXISTS 不會覆寫舊資料）
        schema_sql = (PROJECT_ROOT / 'config' / 'sqlite_schema.sql').read_text(encoding='utf-8')
        conn.executescript(schema_sql)

        files = sorted([p for p in csv_dir.glob('*.csv')
                        if FNAME_RE.match(p.name.lower())])
        if verbose:
            print(f"Scanning {len(files)} CSV files in {csv_dir}")

        for path in files:
            file_info = classify_file(path.name, cfg['city_code_map'])
            if not file_info:
                if verbose:
                    print(f"  skip (unrecognized name): {path.name}")
                continue

            rows, stats = parse_csv_file(path, file_info, cfg)
            inserted = 0
            for table, columns, rec in rows:
                rc = insert_with_columns(conn, table, columns, rec)
                if rc:
                    inserted += 1
                else:
                    total_skipped += 1
            conn.commit()

            stats['inserted'] = inserted
            total_rows += stats['total']
            total_added += inserted
            total_partial += stats['parse_partial']
            total_failed += stats['parse_failed']

            conn.execute(
                """INSERT INTO import_logs
                   (source_file, source_kind, rows_total,
                    records_added, records_skipped,
                    parse_partial, parse_failed, status, message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (path.name, file_info['source_kind'], stats['total'],
                 inserted, stats['total'] - inserted,
                 stats['parse_partial'], stats['parse_failed'],
                 'success',
                 f"{file_info['source_kind']}/{file_info['source_file_type']} city={file_info.get('city')}")
            )
            conn.commit()
            per_file.append((path.name, file_info, stats))

            if verbose:
                print(
                    f"  {path.name:40s} "
                    f"[{file_info['source_kind']}/{file_info['source_file_type']:13s} city={file_info.get('city') or '-':4s}] "
                    f"rows={stats['total']:5d} new={inserted:5d} "
                    f"partial={stats['parse_partial']} failed={stats['parse_failed']}"
                )

    # 總結
    print()
    print("=" * 70)
    print("v2 匯入完成（不過濾、保留所有原始資料）")
    print("=" * 70)
    print(f"檔案總數     : {len(per_file)}")
    print(f"CSV 列總數   : {total_rows}")
    print(f"實際新增     : {total_added}")
    print(f"重複跳過     : {total_skipped}")
    print(f"部分解析     : {total_partial}")
    print(f"解析失敗     : {total_failed}")
    print()
    print("護欄狀態：")
    print(f"  allow_llm                = {ALLOW_LLM}")
    print(f"  allow_worth_score        = {ALLOW_WORTH_SCORE}")
    print(f"  allow_titledeed_judgement = {ALLOW_TITLEDEED_JUDGEMENT}")
    print(f"  filter_at_parse          = {FILTER_AT_PARSE}")

    return {
        'files': len(per_file),
        'rows_total': total_rows,
        'inserted': total_added,
        'skipped': total_skipped,
        'partial': total_partial,
        'failed': total_failed,
    }


if __name__ == '__main__':
    csv_dir_arg = sys.argv[1] if len(sys.argv) > 1 else None
    import_csv_files(csv_dir=csv_dir_arg)
