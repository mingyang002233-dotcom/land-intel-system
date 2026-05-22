#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
批量查詢五大城市實價登錄回報系統
批次查詢台北市、新北市、桃園市、新竹市、台中市全區資料，
並匯出 Excel 及 Telegram 摘要回報。
"""

import sys
import os
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime

# 加入 parent 目錄以載入現有模組
sys.path.insert(0, str(Path(__file__).parent))

from query_land import load_config, parse_natural_query, format_row
from export_report import prepare_export_data, export_excel, generate_filename
from send_telegram_report import load_env_variables, send_telegram_message

# 資料庫路徑
DB_PATH = Path(__file__).parent.parent / 'db' / 'land_intel.db'

# 五大城市
TARGET_CITIES = ['台北市', '新北市', '桃園市', '新竹市', '台中市']

# 明確排除的關鍵詞（只有命中這些詞才排除）
EXCLUSION_KEYWORDS = {
    '道路用地', '道路保留地', '人行步道', '公共設施保留地',
    '學校用地', '國中用地', '國小用地', '公園用地',
    '綠地', '水利用地', '墓地', '交通用地', '停車場用地',
    '協議價購', '政府機關標讓售', '容積代金基金採購',
    '原住民保留地'
}


def is_noise_land_record(row):
    """
    檢查是否為排除記錄
    邏輯：只有命中明確排除詞才排除
    """
    land_use_zone = row.get('land_use_zone') or ''
    note = row.get('note') or ''
    
    # 檢查 land_use_zone 或 note 是否包含排除詞彙
    for keyword in EXCLUSION_KEYWORDS:
        if keyword in land_use_zone or keyword in note:
            return True
    
    return False


def is_target_land_type(row):
    """
    檢查是否應該保留
    邏輯：寧可多保留，不要錯殺
    1. 若 land_use_zone 為 None 或空白 → 保留
    2. 若命中排除詞 → 已在 is_noise_land_record 處理
    3. 其他情況 → 保留
    """
    # 基本上所有非排除的記錄都應保留
    return True


def build_full_query(city, district, period_filter):
    """
    不帶 LIMIT 的完整查詢函式（用於批量查詢）
    :param city: 城市
    :param district: 行政區（None 表示全區）
    :param period_filter: 日期過濾條件
    :return: SQL 查詢結果列表
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 基礎查詢
    query = "SELECT * FROM land_transactions WHERE city = ?"
    params = [city]
    
    # 若指定行政區，則限制查詢
    if district:
        query += " AND district = ?"
        params.append(district)
    
    # 加入日期過濾
    if period_filter:
        query += " AND trade_date >= ?"
        params.append(period_filter)
    
    # 不帶 LIMIT，取全部資料
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in results]


def parse_location(location_raw):
    """解析位置資訊，提取地段和地號"""
    import re
    if not location_raw:
        return '', ''
    
    # 嘗試匹配 "XXX段YYY地號" 的格式
    match = re.search(r'(.+段)(.+地號)', location_raw)
    if match:
        section = match.group(1).strip()
        land_number = match.group(2).replace('地號', '').strip()
        return section, land_number
    
    # 備用：尋找 "段"
    segment_match = re.search(r'(.+段)', location_raw)
    if segment_match:
        section = segment_match.group(1).strip()
        remaining = location_raw.replace(section, '').strip()
        return section, remaining
    
    return '', location_raw


def prepare_export_data_simple(records):
    """簡化的匯出資料準備（只含基本欄位，不含投資判斷）"""
    export_data = []
    for record in records:
        section, land_number = parse_location(record.get('location_raw', ''))
        export_data.append({
            '交易日期': record['trade_date'],
            '縣市': record['city'],
            '行政區': record['district'],
            '地段': section,
            '地號': land_number,
            '坪數': record.get('area_ping', ''),
            '單價(萬/坪)': record.get('unit_price_per_ping_wan', ''),
            '總價(萬)': record.get('total_price_wan', ''),
            '使用分區': record.get('land_use_zone') or '',
            '備註': record.get('note') or ''
        })
    return export_data


def filter_records(records):
    """過濾記錄：排除明確排除詞，保留其他"""
    filtered = []
    for record in records:
        # 如果命中排除詞，排除
        if is_noise_land_record(record):
            continue
        # 其他情況保留
        filtered.append(record)
    return filtered


def batch_query_cities(period_str, db_path=None):
    """
    批次查詢五大城市
    :param period_str: 期間字串，例如 "近半年"
    :param db_path: 資料庫路徑
    :return: {city: [filtered_records], ...}, period_filter
    """
    if db_path is None:
        db_path = DB_PATH
    
    # 解析期間字串，取得日期過濾條件
    config = load_config()
    parsed, _ = parse_natural_query(period_str, config)
    period_filter = parsed.get('start_date')
    
    city_data = {}
    for city in TARGET_CITIES:
        # 查詢該城市全區資料
        records = build_full_query(city, None, period_filter)
        # 過濾雜訊
        filtered = filter_records(records)
        city_data[city] = filtered
    
    return city_data, period_filter


def export_city_reports(city_data, period_str, output_dir=None):
    """
    匯出各城市 Excel 及總表
    :param city_data: {city: [records], ...}
    :param period_str: 期間字串
    :param output_dir: 輸出目錄
    :return: {city: filename, 'summary': summary_filename}
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / 'output'
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    today = datetime.now().strftime('%Y%m%d')
    exported_files = {}
    all_data = []
    
    # 各城市分別匯出
    for city in TARGET_CITIES:
        records = city_data.get(city, [])
        if not records:
            continue
        
        # 準備匯出資料（使用簡化版，不含投資判斷）
        export_data = prepare_export_data_simple(records)
        
        # 產生檔名
        city_filename_part = city.replace('市', '_city')
        filename = f"{city_filename_part}_{today}.xlsx"
        filepath = output_dir / filename
        
        # 匯出 Excel
        export_excel(export_data, filepath)
        exported_files[city] = filename
        
        # 蒐集所有城市資料用於總表
        for record in records:
            all_data.append(record)
    
    # 匯出五大城市總表
    if all_data:
        summary_export_data = prepare_export_data_simple(all_data)
        summary_filename = f"five_city_summary_{today}.xlsx"
        summary_filepath = output_dir / summary_filename
        export_excel(summary_export_data, summary_filepath)
        exported_files['summary'] = summary_filename
    
    return exported_files


def generate_telegram_summary(city_data, period_str, exported_files):
    """
    生成 Telegram 摘要報告
    :param city_data: {city: [records], ...}
    :param period_str: 期間字串
    :param exported_files: {city: filename, 'summary': summary_filename}
    :return: 摘要文字
    """
    lines = [
        '📊 老蕭 LAND 實價登錄查詢回報',
        '',
        f'查詢期間：{period_str}',
        ''
    ]
    
    # 各城市筆數
    for city in TARGET_CITIES:
        count = len(city_data.get(city, []))
        lines.append(f'{city}：{count} 筆')
    
    lines.extend([
        '',
        '資料保留：',
        '農地／建地／住宅區／商業區／工業地／產業專用區',
        '',
        '已排除：',
        '道路用地、公共設施保留地、學校用地、公園綠地、協議價購等非主要開發標的',
        '',
        'Excel 報表：'
    ])
    
    # 列出導出的檔案
    for city in TARGET_CITIES:
        if city in exported_files:
            lines.append(f'- {exported_files[city]}')
    
    if 'summary' in exported_files:
        lines.append(f'- {exported_files["summary"]}')
    
    lines.extend([
        '',
        '老蕭 LAND｜實價登錄查詢系統'
    ])
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='批量查詢五大城市實價登錄並回報')
    parser.add_argument('period', nargs='+', help='查詢期間，例如：近半年')
    parser.add_argument('--db', default=str(DB_PATH), help='SQLite 資料庫路徑')
    parser.add_argument('--output', help='輸出目錄（預設為 output/）')
    parser.add_argument('--skip-telegram', action='store_true', help='跳過 Telegram 推送')
    args = parser.parse_args()
    
    period_str = ' '.join(args.period)
    
    print(f'🔍 開始批量查詢五大城市，期間：{period_str}')
    
    # 查詢五大城市資料
    try:
        city_data, period_filter = batch_query_cities(period_str, args.db)
        print(f'✅ 查詢完成')
        
        # 顯示各城市筆數
        for city in TARGET_CITIES:
            count = len(city_data.get(city, []))
            print(f'  {city}：{count} 筆')
    except Exception as e:
        print(f'❌ 查詢失敗：{e}')
        sys.exit(1)
    
    # 匯出 Excel
    print(f'📊 開始匯出 Excel...')
    try:
        exported_files = export_city_reports(city_data, period_str, args.output)
        print(f'✅ Excel 匯出完成')
        for city, filename in exported_files.items():
            print(f'  {city}: {filename}')
    except Exception as e:
        print(f'❌ Excel 匯出失敗：{e}')
        sys.exit(1)
    
    # 生成 Telegram 摘要
    summary = generate_telegram_summary(city_data, period_str, exported_files)
    print(f'\n📝 Telegram 摘要：\n{summary}\n')
    
    # Telegram 推送
    if not args.skip_telegram:
        print(f'📤 推送 Telegram 摘要...')
        try:
            load_env_variables()
            token = os.environ.get('TELEGRAM_BOT_TOKEN')
            chat_id = os.environ.get('TELEGRAM_CHAT_ID')
            if not token:
                raise ValueError('未設定環境變數 TELEGRAM_BOT_TOKEN')
            if not chat_id:
                raise ValueError('未設定環境變數 TELEGRAM_CHAT_ID')
            send_telegram_message(token, chat_id, summary)
            print(f'✅ Telegram 推送完成')
        except Exception as e:
            print(f'⚠️  Telegram 推送失敗：{e}')
            print(f'   請檢查 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID')


if __name__ == '__main__':
    main()
