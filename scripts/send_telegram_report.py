#!/usr/bin/env python3

import argparse
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from query_land import load_config, parse_natural_query, build_query, format_row
import export_report

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
OUTPUT_DIR = PROJECT_ROOT / 'output'


def load_dotenv(path):
    if not path.exists():
        return {}
    env = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_env_variables():
    env_paths = [PROJECT_ROOT / '.env', PROJECT_ROOT.parent / '.env']
    loaded = {}
    for path in env_paths:
        if path.exists():
            loaded = load_dotenv(path)
            print(f'載入環境變數：{path}')
            break
    for key, value in loaded.items():
        if key not in os.environ or not os.environ.get(key):
            os.environ[key] = value


def extract_period_text(query_text):
    if '近半年' in query_text:
        return '近半年'
    if '今年' in query_text:
        return '今年'
    if '三個月' in query_text or '三月' in query_text:
        return '近三個月'
    custom = None
    import re
    match = re.search(r'(\d+)\s*個月', query_text)
    if match:
        custom = f'{match.group(1)} 個月'
    return custom or '自訂期間'


def _format_land_card(idx, row):
    """單筆土地成交卡片，格式：縣市行政區｜地段｜地號 / 坪數｜總價｜單價 / 成交日 / 備註"""
    import re as _re
    city = row.get('city') or ''
    district = row.get('district') or ''
    section = row.get('section_name') or ''
    land_num = row.get('land_number') or ''
    loc_raw = row.get('location_raw') or ''
    trade_date = row.get('trade_date') or ''
    area_ping = row.get('area_ping')
    total_wan = row.get('total_price_wan')
    unit_price = row.get('unit_price_per_ping_wan')
    land_use_zone = row.get('land_use_zone') or ''
    note = row.get('note') or ''

    # 從 location_raw 補抓地號
    if not land_num and '地號' in loc_raw:
        m = _re.search(r'(\d[\d\-\/]*)\s*地號', loc_raw)
        if m:
            land_num = m.group(1)

    place = f'{city}{district}'
    section_part = section if section else ''
    land_part = f'{land_num}地號' if land_num else ''

    header = f'{idx}. {place}｜{section_part}｜{land_part}' if (section_part or land_part) else f'{idx}. {place}'
    area_str = f'{area_ping:.1f}坪' if area_ping else 'N/A'
    total_str = _fmt_total(total_wan)
    unit_str = f'{unit_price:.1f}萬/坪' if unit_price else 'N/A'
    zone_str = f'（{land_use_zone}）' if land_use_zone else ''

    lines = [
        header,
        f'   {area_str}｜{total_str}｜{unit_str}{zone_str}',
        f'   成交：{trade_date}',
    ]
    if note:
        lines.append(f'   備註：{note}')
    lines.append('')
    return '\n'.join(lines)


def _fmt_total(total_wan):
    if total_wan is None:
        return 'N/A'
    if total_wan >= 10000:
        return f'{total_wan / 10000:.2f}億'
    return f'{total_wan:.0f}萬'


def build_message(query_text, params, results, xlsx_path=None):
    area_line = []
    if params.get('city'):
        area_line.append(params['city'])
    if params.get('district'):
        area_line.append(params['district'])
    area_text = ' '.join(area_line) if area_line else query_text

    period_text = extract_period_text(query_text)
    total = len(results)
    lines = [
        '🛰 老蕭 LAND 實價登錄快訊',
        '',
        f'查詢區域：{area_text}　期間：{period_text}',
        f'共 {total} 筆成交',
        '',
        '重點案件：',
        '',
    ]

    for idx, row in enumerate(results[:10], 1):
        lines.append(_format_land_card(idx, row))

    if total > 10:
        lines.append(f'（僅顯示前 10 筆，共 {total} 筆）')
        lines.append('')

    if xlsx_path:
        lines.append(f'完整報表：{xlsx_path}')

    return '\n'.join(lines)


def send_telegram_message(token, chat_id, text):
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }).encode('utf-8')
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            response_text = resp.read().decode('utf-8')
            return True, response_text
    except Exception as exc:
        return False, str(exc)


def main():
    parser = argparse.ArgumentParser(description='發送 Telegram 土地成交快訊')
    parser.add_argument('query', nargs='+', help='例如：桃園市 大園區 近半年')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite 資料庫路徑')
    args = parser.parse_args()

    load_env_variables()
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token:
        print('錯誤：未設定環境變數 TELEGRAM_BOT_TOKEN。')
        print('請在 land-intel-system/.env 或 ../.env 中設定，或透過環境變數 export。')
        sys.exit(1)
    if not chat_id:
        print('錯誤：未設定環境變數 TELEGRAM_CHAT_ID。')
        print('請在 land-intel-system/.env 或 ../.env 中設定，或透過環境變數 export。')
        sys.exit(1)

    config = load_config()
    query_text = ' '.join(args.query).strip()
    params, _ = parse_natural_query(query_text, config)
    if 'city' not in params and 'section_name' not in params and 'keyword' not in params:
        print('請在查詢字串中指定查詢範圍，例如：台北市、新林段、三塊石。')
        sys.exit(1)

    sql, values = build_query(params)
    with sqlite3.connect(args.db) as conn:
        cursor = conn.execute(sql, values)
        rows = cursor.fetchall()

    results = [format_row(row, config['excluded_note_terms']) for row in rows]

    xlsx_path = None
    if results:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        export_data = export_report.prepare_export_data(results)
        xlsx_filename = export_report.generate_filename(query_text, 'xlsx')
        xlsx_path = export_report.export_excel(export_data, xlsx_filename)
        print(f'Excel 報表已匯出：{xlsx_path}')
    else:
        print('查詢無資料，僅發送空報表提醒訊息。')

    message = build_message(query_text, params, results, xlsx_path)
    success, response = send_telegram_message(token, chat_id, message)
    if success:
        print('Telegram 推播成功。')
        print(response)
    else:
        print('Telegram 推播失敗：')
        print(response)
        sys.exit(1)


if __name__ == '__main__':
    main()
