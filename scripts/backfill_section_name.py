#!/usr/bin/env python3
"""
backfill_section_name.py
回填或修正 section_name 欄位：

Step 1：清除誤判為地段名的路段（section_name 含路名格式，且 location_raw 無「地號」）
Step 2：回填 section_name 為 NULL／空白的土地位置記錄（location_raw 含「地號」）

注意：
- location_raw 含 PUA 字元時，無法確認地段名稱，section_name 保持 NULL
- 不做 PUA → 標準字的自動替換（字元身分未確認）
"""
import sqlite3
import sys
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir))

from parse_realprice import DB_PATH, extract_section_and_number, has_pua_chars


def normalize_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def trim_city_district(location_raw, city, district):
    """城市／行政區前綴去除（與 parse_main_row 邏輯一致）。"""
    text = location_raw
    if text and city:
        for variant in [city, city.replace('台', '臺') if '台' in city else city.replace('臺', '台')]:
            if text.startswith(variant):
                text = text[len(variant):].strip()
                break
    if text and district:
        for variant in [district, district.replace('台', '臺') if '台' in district else district.replace('臺', '台')]:
            if text.startswith(variant):
                text = text[len(variant):].strip()
                break
    return text


def is_road_section_name(section_name, location_raw):
    """
    判斷 section_name 是否為誤判：
    - section_name 含路名格式（路／街／大道）
    - 且 location_raw 不含「地號」（代表是門牌地址，非土地地號）
    """
    if not section_name:
        return False
    import re
    has_road = bool(re.search(r'(?:路|街|大道|公路|快速道路|高速公路)', section_name))
    no_dihao = '地號' not in (location_raw or '')
    return has_road and no_dihao


def backfill_section_names(verbose=True):
    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # -------------------------------------------------------
    # Step 1：清除誤判為地段名的路段
    # -------------------------------------------------------
    wrong_rows = cur.execute(
        "SELECT rowid, section_name, location_raw FROM land_transactions "
        "WHERE section_name IS NOT NULL AND TRIM(section_name) != '' "
        "AND location_raw NOT LIKE '%地號%'"
    ).fetchall()
    cleared = 0
    for rowid, section_name, location_raw in wrong_rows:
        if is_road_section_name(section_name, location_raw):
            cur.execute(
                "UPDATE land_transactions SET section_name = NULL, land_number = NULL WHERE rowid = ?",
                (rowid,),
            )
            cleared += 1
    conn.commit()
    if verbose:
        print(f"Step 1：清除路名誤判筆數 = {cleared}")

    # -------------------------------------------------------
    # Step 2：回填 section_name 為 NULL／空白的記錄
    # -------------------------------------------------------
    before_null = cur.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE section_name IS NULL OR TRIM(section_name) = ''"
    ).fetchone()[0]

    rows = cur.execute(
        "SELECT rowid, location_raw, section_name, land_number, city, district FROM land_transactions "
        "WHERE (section_name IS NULL OR TRIM(section_name) = '') AND location_raw LIKE '%地號%'"
    ).fetchall()

    updated = 0
    pua_skipped = 0
    for rowid, location_raw, section_name, land_number, city, district in rows:
        if not location_raw or not str(location_raw).strip():
            continue

        # PUA 字元：交由 extract_section_and_number 內的 normalize_pua 處理
        if has_pua_chars(location_raw):
            pua_skipped += 1
            if verbose and pua_skipped <= 5:
                print(f'  [PUA 處理] location_raw={repr(location_raw)}')

        trimmed = trim_city_district(str(location_raw).strip(), city, district)
        new_section, new_land = extract_section_and_number(trimmed)
        new_section = normalize_text(new_section)
        new_land = normalize_text(new_land)

        if new_section is None and new_land is None:
            continue

        cur.execute(
            "UPDATE land_transactions SET section_name = ?, land_number = ? WHERE rowid = ?",
            (new_section, new_land, rowid),
        )
        updated += 1

    conn.commit()

    after_null = cur.execute(
        "SELECT COUNT(*) FROM land_transactions WHERE section_name IS NULL OR TRIM(section_name) = ''"
    ).fetchone()[0]

    if verbose:
        print(f"Step 2：回填前 NULL/空白筆數 = {before_null}")
        print(f"Step 2：成功回填筆數 = {updated}")
        print(f"Step 2：PUA 字元（正規化處理）筆數 = {pua_skipped}")
        print(f"Step 2：回填後 NULL/空白筆數 = {after_null}")
        print()

    # -------------------------------------------------------
    # 診斷：顯示 大園區 地段統計
    # -------------------------------------------------------
    if verbose:
        print("桃園市 / 大園區 section_name 統計：")
        for section_name, cnt in cur.execute(
            "SELECT section_name, COUNT(*) FROM land_transactions "
            "WHERE city = '桃園市' AND district LIKE '%大園%' "
            "GROUP BY section_name ORDER BY COUNT(*) DESC"
        ).fetchall():
            flag = '[PUA]' if has_pua_chars(section_name or '') else ''
            print(f"  {section_name or '<EMPTY>'}: {cnt} {flag}")

    conn.close()


if __name__ == '__main__':
    backfill_section_names()
