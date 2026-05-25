"""
apply_subsection_normalize.py
以官方地段代碼 CSV 為標準，驗證並清理 SQLite sub_section 欄位。

執行模式：
  python apply_subsection_normalize.py          → dry-run（預設）
  python apply_subsection_normalize.py --apply  → 正式 UPDATE SQLite + 匯出 Excel

分類規則：
  clear_nan     sub=nan/空字串               → 清空（無爭議）
  clear_no_sub  官方確認該縣市+區域+地段無小段 → 清空
  keep          官方有小段且命中              → 保留不動
  flag_unclear  官方有小段但未命中            → 寫 sys_judgment，不清空
  flag_unknown  官方查無此鍵                 → 寫 sys_judgment，不清空

比對 normalize（僅用於比對，不寫回）：
  1. city alias：從官方 CSV 實際縣市值建立台/臺雙向對照
  2. sub_section 去括號代碼：三角子小段(0307) → 三角子小段
  3. sub_section 去 section_raw 前綴：信華段三小段 → 三小段
  4. 官方小段名「三」同時接受「三小段」

比對鍵必須使用完整四鍵：縣市 + 區域 + 地段 + 小段
不可單用小段名稱全表比對。
"""

import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH    = Path("/Users/xiaomingyang/projects/land-intel-system/data/database/land_master.db")
REF_CSV    = Path("/Users/xiaomingyang/projects/land-intel-system/data/reference/land_section_codes.csv")
LOG_DIR    = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/output")
BACKUP_DIR = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/最新完成版/backup")

APPLY_MODE = '--apply' in sys.argv
LOG_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP  = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_PATH   = LOG_DIR / f"subsection_normalize_log_{TIMESTAMP}.csv"
EXCEL_OUT  = LOG_DIR / f"land_master_subsection_{TIMESTAMP}.xlsx"
DB_BACKUP  = BACKUP_DIR / f"land_master_backup_{TIMESTAMP}.db"

BRACKET_RE = re.compile(r'^(.+?)\s*\(\d{4}\)$')

# ── 官方參照表 ───────────────────────────────────────────────────
print("載入官方地段參照表…")
ref = pd.read_csv(REF_CSV, dtype=str).fillna('')

# city alias：從官方實際值建立，不硬轉台/臺
ref_cities = set(ref['縣市名稱'].unique())
city_alias = {}
for c in ref_cities:
    city_alias[c] = c
    alt = c.replace('臺', '台') if '臺' in c else c.replace('台', '臺')
    if alt not in ref_cities:
        city_alias[alt] = c

def norm_city(s):
    return city_alias.get(str(s or '').strip(), str(s or '').strip())

def strip_bracket(s):
    m = BRACKET_RE.match(str(s or '').strip())
    return m.group(1).strip() if m else str(s or '').strip()

def strip_sec_prefix(sub, sec):
    if sec and sub.startswith(sec):
        return sub[len(sec):].strip()
    return sub

# 建立官方鍵：(縣市, 鄉鎮市區, 段名+段) → set of 可比對小段名
valid_subs      = defaultdict(set)
official_has_sub = {}

for _, row in ref.iterrows():
    city     = str(row['縣市名稱']).strip()
    dist     = str(row['鄉鎮市區名稱']).strip()
    sec      = str(row['段名']).strip()
    sub      = str(row['小段名']).strip()
    sec_full = sec + '段' if not sec.endswith('段') else sec
    key      = (city, dist, sec_full)
    if sub:
        valid_subs[key].add(sub)
        valid_subs[key].add(sub + '小段')  # 「三」→ 也接受「三小段」
        official_has_sub[key] = True
    elif key not in official_has_sub:
        official_has_sub[key] = False

print(f"  官方鍵總數：{len(official_has_sub):,}（有小段：{sum(v for v in official_has_sub.values()):,}）")

# ── 讀取 SQLite 全表 ────────────────────────────────────────────
print("讀取 SQLite…")
t0 = time.time()
conn = sqlite3.connect(str(DB_PATH))

cols = [r[1] for r in conn.execute("PRAGMA table_info(land_master)").fetchall()]
has_sys = 'sys_judgment' in cols
print(f"  sys_judgment 欄位：{'已存在' if has_sys else '不存在（--apply 時會新增）'}")

df = pd.read_sql("SELECT id, city, district, section_raw, sub_section, sys_judgment FROM land_master", conn)
conn.close()
print(f"  {len(df):,} 筆  ({time.time()-t0:.1f}s)")

# ── 分類 ────────────────────────────────────────────────────────
print("分類中…")
t1 = time.time()
clear_nan, clear_no_sub, keep, flag_unclear, flag_unknown = [], [], [], [], []

for _, r in df.iterrows():
    city    = norm_city(r['city'])
    dist    = str(r['district'] or '').strip()
    sec     = str(r['section_raw'] or '').strip()
    sub_raw = str(r['sub_section'] or '').strip()

    if not sub_raw or sub_raw.lower() in ('nan', 'none', ''):
        clear_nan.append(r)
        continue

    sub_nb = strip_bracket(sub_raw)
    sub_c  = strip_sec_prefix(sub_nb, sec)
    key    = (city, dist, sec)

    if key not in official_has_sub:
        flag_unknown.append(dict(r, _sub_clean=sub_c))
    elif not official_has_sub[key]:
        clear_no_sub.append(dict(r, _sub_clean=sub_c))
    elif sub_c in valid_subs[key] or sub_raw in valid_subs[key] or sub_nb in valid_subs[key]:
        keep.append(dict(r, _sub_clean=sub_c))
    else:
        flag_unclear.append(dict(r, _sub_clean=sub_c))

print(f"  完成  {time.time()-t1:.1f}s")

# ── DRY-RUN 統計 ────────────────────────────────────────────────
print()
print("=== DRY-RUN 統計 ===")
print(f"  sub=nan/空字串 → 應清空            : {len(clear_nan):>7,}")
print(f"  官方確認無小段 → 應清空            : {len(clear_no_sub):>7,}")
print(f"  官方有小段且命中 → 保留            : {len(keep):>7,}")
print(f"  官方有小段但未命中 → 待確認        : {len(flag_unclear):>7,}")
print(f"  官方查無此鍵 → 待確認              : {len(flag_unknown):>7,}")
print(f"  ──────────────────────────────────")
print(f"  合計清空                           : {len(clear_nan)+len(clear_no_sub):>7,}")
print(f"  合計標記（不清空）                 : {len(flag_unclear)+len(flag_unknown):>7,}")
print()
print(f"  sys_judgment：{'已存在' if has_sys else '需新增'}")
print(f"  change_log 路徑：{LOG_PATH}")
print(f"  匯出 Excel 路徑：{EXCEL_OUT}")
print(f"  SQLite 備份路徑：{DB_BACKUP}")

if not APPLY_MODE:
    print()
    print("=== DRY-RUN 完成，未修改任何資料 ===")
    print("正式執行請加 --apply 參數。")
    sys.exit(0)

# ════════════════════════════════════════════════════════════════
# 以下為 --apply 模式
# ════════════════════════════════════════════════════════════════

# ── 備份 ─────────────────────────────────────────────────────────
print(f"\n備份 SQLite → {DB_BACKUP}")
shutil.copy2(DB_PATH, DB_BACKUP)
print(f"  完成（{DB_BACKUP.stat().st_size/1024/1024:.1f} MB）")

# ── ALTER TABLE ──────────────────────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))
cur  = conn.cursor()
if not has_sys:
    print("ALTER TABLE 新增 sys_judgment…")
    cur.execute("ALTER TABLE land_master ADD COLUMN sys_judgment TEXT")
    conn.commit()

# ── UPDATE：清空 clear_nan ───────────────────────────────────────
print(f"\nUPDATE clear_nan {len(clear_nan):,} 筆…")
t2 = time.time()
cur.executemany(
    "UPDATE land_master SET sub_section=NULL WHERE id=?",
    [(int(r['id']),) for r in clear_nan]
)
conn.commit()
print(f"  完成  {time.time()-t2:.1f}s")

# ── UPDATE：清空 clear_no_sub ────────────────────────────────────
print(f"UPDATE clear_no_sub {len(clear_no_sub):,} 筆…")
t3 = time.time()
cur.executemany(
    "UPDATE land_master SET sub_section=NULL WHERE id=?",
    [(int(r['id']),) for r in clear_no_sub]
)
conn.commit()
print(f"  完成  {time.time()-t3:.1f}s")

# ── UPDATE：標記 flag_unclear ────────────────────────────────────
print(f"UPDATE flag_unclear {len(flag_unclear)} 筆…")
cur.executemany(
    "UPDATE land_master SET sys_judgment='小段待確認（官方有小段但未命中）' WHERE id=?",
    [(int(r['id']),) for r in flag_unclear]
)
conn.commit()

# ── UPDATE：標記 flag_unknown ────────────────────────────────────
print(f"UPDATE flag_unknown {len(flag_unknown)} 筆…")
cur.executemany(
    "UPDATE land_master SET sys_judgment='小段待確認（官方查無此鍵）' WHERE id=?",
    [(int(r['id']),) for r in flag_unknown]
)
conn.commit()
conn.close()

# ── change_log ────────────────────────────────────────────────────
rows_log = []
for r in clear_nan:
    rows_log.append({'id': r['id'], 'action': 'clear_nan', 'city': r['city'],
                     'district': r['district'], 'section_raw': r['section_raw'],
                     'old_sub': r['sub_section'], 'new_sub': None, 'sys_judgment': None})
for r in clear_no_sub:
    rows_log.append({'id': r['id'], 'action': 'clear_no_sub', 'city': r['city'],
                     'district': r['district'], 'section_raw': r['section_raw'],
                     'old_sub': r['sub_section'], 'new_sub': None, 'sys_judgment': None})
for r in flag_unclear:
    rows_log.append({'id': r['id'], 'action': 'flag_unclear', 'city': r['city'],
                     'district': r['district'], 'section_raw': r['section_raw'],
                     'old_sub': r['sub_section'], 'new_sub': r['sub_section'],
                     'sys_judgment': '小段待確認（官方有小段但未命中）'})
for r in flag_unknown:
    rows_log.append({'id': r['id'], 'action': 'flag_unknown', 'city': r['city'],
                     'district': r['district'], 'section_raw': r['section_raw'],
                     'old_sub': r['sub_section'], 'new_sub': r['sub_section'],
                     'sys_judgment': '小段待確認（官方查無此鍵）'})

pd.DataFrame(rows_log).to_csv(LOG_PATH, index=False, encoding='utf-8-sig')
print(f"\nchange_log：{len(rows_log):,} 筆 → {LOG_PATH}")

# ── 匯出 Excel ────────────────────────────────────────────────────
print("匯出 Excel 檢視檔…")
t4 = time.time()
conn2 = sqlite3.connect(str(DB_PATH))
df_out = pd.read_sql("""
SELECT
    sys_judgment     AS 系統判定,
    updated_at       AS 更新日期,
    zone_type        AS 分區,
    location_tag     AS 位置,
    city             AS 縣市,
    district         AS 地區,
    section_raw      AS 地段,
    sub_section      AS 小段,
    land_no_raw      AS 地號,
    announced_value  AS 公告現值,
    reg_seq          AS 次序,
    reg_date         AS 登記日期,
    reg_reason       AS 登記原因,
    cause_date       AS 發生日期,
    owner_name       AS 所有權人,
    owner_id_full    AS 統一編號（完整）,
    postal_code      AS 郵遞區號,
    address          AS 住址,
    is_sold          AS 已售出,
    share_denom      AS 分母,
    share_numer      AS 分子,
    purchase_price   AS 持分,
    actual_owned_area AS 持分坪數,
    total_area_ping  AS 土地總坪數,
    note             AS 備註,
    phone            AS 電話
FROM land_master ORDER BY id
""", conn2)
conn2.close()
df_out.to_excel(EXCEL_OUT, index=False, engine='openpyxl')
print(f"  {len(df_out):,} 行  ({time.time()-t4:.1f}s)  → {EXCEL_OUT}")

print(f"\n總耗時：{time.time()-t0:.1f}s")
print()
print("=== 執行完成 ===")
print(f"  清空（nan/空）       : {len(clear_nan):,}")
print(f"  清空（官方無小段）   : {len(clear_no_sub):,}")
print(f"  合計清空             : {len(clear_nan)+len(clear_no_sub):,}")
print(f"  待確認標記（未命中） : {len(flag_unclear)}")
print(f"  待確認標記（查無鍵） : {len(flag_unknown)}")
print(f"  保留不動             : {len(keep):,}")
print(f"  SQLite 備份          : {DB_BACKUP}")
print(f"  change_log           : {LOG_PATH}")
print(f"  新版 Excel           : {EXCEL_OUT}")
