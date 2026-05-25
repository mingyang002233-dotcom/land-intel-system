#!/usr/bin/env python3
"""
Migration: 開發觀察區 location_tag 補標
執行日期：2026-05-26
更新筆數：7,671 筆

規則：
  - 只更新 location_tag IS NULL 或空白的資料
  - 已有 location_tag 的資料一律保留，不覆蓋
  - 不新增假地號，不改 schema

用法：
  python3 scripts/migrate_dev_zone_tags.py           # 正式寫入
  python3 scripts/migrate_dev_zone_tags.py --dry-run # 僅模擬，不寫入
"""

import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'data' / 'database' / 'land_master.db'

# 觀察區分類定義
# 格式：(tag, city, district, section_raw, sub_section_or_None)
# sub_section 有值時：精確比對 section_raw + sub_section
# sub_section 為 None 時：只比對 section_raw
ZONE_RULES = [
    # ── 市府二期 ──────────────────────────────────────────────────
    # 航空城市府二期核心觀察區，不等同官方正式公告二期範圍
    ('市府二期', '桃園市', '大園區', '竹圍段',   '三塊石小段'),
    ('市府二期', '桃園市', '大園區', '竹圍段',   '拔子林小段'),
    ('市府二期', '桃園市', '大園區', '竹圍段',   '四股小段'),
    ('市府二期', '桃園市', '大園區', '大牛稠段', '倒厝子小段'),
    ('市府二期', '桃園市', '大園區', '大牛稠段', '大牛稠小段'),
    ('市府二期', '桃園市', '大園區', '八股段',   None),
    ('市府二期', '桃園市', '蘆竹區', '內興段',   None),

    # ── 捷運綠線 ──────────────────────────────────────────────────
    # 桃園捷運綠線 G12-G13 核心開發／區段徵收觀察區
    ('捷運綠線', '桃園市', '蘆竹區', '南竹段', None),
    ('捷運綠線', '桃園市', '蘆竹區', '河底段', None),
    ('捷運綠線', '桃園市', '蘆竹區', '西埔段', None),
    ('捷運綠線', '桃園市', '蘆竹區', '富興段', None),
    ('捷運綠線', '桃園市', '蘆竹區', '南庄段', None),
    ('捷運綠線', '桃園市', '蘆竹區', '蘆興段', None),
    ('捷運綠線', '桃園市', '桃園區', '和平段', None),

    # ── 台鐵紅線 ──────────────────────────────────────────────────
    # 台鐵紅線／中路站區段徵收核心觀察區
    ('台鐵紅線', '桃園市', '桃園區', '皮寮段', None),
    ('台鐵紅線', '桃園市', '桃園區', '國際段', None),
    ('台鐵紅線', '桃園市', '桃園區', '玉山段', None),
    ('台鐵紅線', '桃園市', '桃園區', '中路段', None),

    # ── 宜蘭高鐵 ──────────────────────────────────────────────────
    # 宜蘭高鐵縣政中心版核心觀察區
    # 振興段注意：重測後段名，新舊段名可能並存
    ('宜蘭高鐵', '宜蘭縣', '壯圍鄉', '凱旋一段', None),
    ('宜蘭高鐵', '宜蘭縣', '壯圍鄉', '凱旋二段', None),
    ('宜蘭高鐵', '宜蘭縣', '壯圍鄉', '新福段',   None),
    ('宜蘭高鐵', '宜蘭縣', '壯圍鄉', '振興段',   None),
]


def backup_db(db_path: Path) -> Path:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = db_path.parent / f'land_master_backup_{ts}_dev_zone_tags.db'
    shutil.copy2(db_path, backup)
    return backup


def run(dry_run: bool = False):
    if not DB_PATH.exists():
        raise FileNotFoundError(f'DB 不存在：{DB_PATH}')

    if not dry_run:
        backup = backup_db(DB_PATH)
        print(f'✅ 備份完成：{backup}')

    con = sqlite3.connect(DB_PATH)
    if not dry_run:
        con.execute('BEGIN')

    totals: dict[str, int] = {}
    zero_hit: list[str] = []

    for tag, city, district, section, sub in ZONE_RULES:
        if sub:
            sql_count = (
                'SELECT COUNT(*) FROM land_master '
                'WHERE city=? AND district=? AND section_raw=? AND sub_section=? '
                "AND (location_tag IS NULL OR location_tag='')"
            )
            params = (city, district, section, sub)
            label = f'{city}{district}{section}{sub}'
        else:
            sql_count = (
                'SELECT COUNT(*) FROM land_master '
                'WHERE city=? AND district=? AND section_raw=? '
                "AND (location_tag IS NULL OR location_tag='')"
            )
            params = (city, district, section)
            label = f'{city}{district}{section}'

        cnt = con.execute(sql_count, params).fetchone()[0]

        if cnt == 0:
            zero_hit.append(label)
            print(f'  [{tag}] {label} → 0 筆（DB 無資料或已全部標記）')
            continue

        if not dry_run:
            if sub:
                con.execute(
                    f"UPDATE land_master SET location_tag=? "
                    f"WHERE city=? AND district=? AND section_raw=? AND sub_section=? "
                    f"AND (location_tag IS NULL OR location_tag='')",
                    (tag, city, district, section, sub)
                )
            else:
                con.execute(
                    f"UPDATE land_master SET location_tag=? "
                    f"WHERE city=? AND district=? AND section_raw=? "
                    f"AND (location_tag IS NULL OR location_tag='')",
                    (tag, city, district, section)
                )

        totals[tag] = totals.get(tag, 0) + cnt
        mode = '[DRY-RUN]' if dry_run else ''
        print(f'  {mode}[{tag}] {label} → {cnt} 筆')

    if not dry_run:
        con.execute('COMMIT')
        print('\n✅ COMMIT 完成')
    else:
        print('\n⚠️  DRY-RUN 模式，未寫入任何資料')

    con.close()

    print('\n=== 各分類合計 ===')
    grand = 0
    for tag, cnt in totals.items():
        print(f'  {tag}: {cnt:,} 筆')
        grand += cnt
    print(f'  總計: {grand:,} 筆')

    if zero_hit:
        print('\n=== 零命中地段（資料尚未匯入 DB）===')
        for s in zero_hit:
            print(f'  {s}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='開發觀察區 location_tag 補標 migration')
    parser.add_argument('--dry-run', action='store_true', help='模擬模式，不寫入 DB')
    args = parser.parse_args()
    run(dry_run=args.dry_run)
