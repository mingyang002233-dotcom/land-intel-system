"""
apply_land_section_normalize.py
地段/小段 normalize — SQLite 為核心修正層

執行模式：
  python apply_land_section_normalize.py          → dry-run（預設）
  python apply_land_section_normalize.py --apply  → 正式 UPDATE SQLite + 匯出 Excel

流程：
  1. SQLite 備份
  2. 載入官方段代碼 CSV + SQLite 全表
  3. classify 每一列（A/B/ok）
  4. dry-run：印統計，不動任何資料
  5. --apply：
     a. A類 → UPDATE section_raw, sub_section WHERE id=?
     b. B類 → UPDATE sys_judgment WHERE id=?
     c. 輸出 change_log.csv
     d. 匯出新版 Excel（不覆蓋原 MASTER）
"""

import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH   = Path("/Users/xiaomingyang/projects/land-intel-system/data/database/land_master.db")
REF_CSV   = Path("/Users/xiaomingyang/projects/land-intel-system/data/reference/land_section_codes.csv")
LOG_DIR    = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/output")
EXCEL_DIR  = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/終極版/每月快照")
BACKUP_DIR = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/最新完成版/backup")

APPLY_MODE = '--apply' in sys.argv
LOG_DIR.mkdir(parents=True, exist_ok=True)
EXCEL_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

TIMESTAMP = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_PATH  = LOG_DIR / f"section_normalize_log_{TIMESTAMP}.csv"
EXCEL_OUT = EXCEL_DIR / f"land_master_normalized_{TIMESTAMP}.xlsx"
DB_BACKUP = BACKUP_DIR / f"land_master_backup_{TIMESTAMP}.db"

# ── 官方段代碼參照表 ────────────────────────────────────────────
print("載入官方地段參照表…")
ref = pd.read_csv(REF_CSV, dtype=str).fillna('')

def norm_city(s):
    return str(s).replace('台', '臺').strip()

valid_set = set()
for _, row in ref.iterrows():
    city = norm_city(row['縣市名稱'])
    sec  = str(row['段名']).strip()
    if sec:
        valid_set.add((city, sec + '段'))
        valid_set.add((city, sec))

print(f"  官方段名集合：{len(valid_set):,} 條目")

BRACKET_RE = re.compile(r'^(.+?)\s*\(\d{4}\)$')
MIXED_RE   = re.compile(r'^(.+?段)(.+?小段)$')

def classify(city_raw, sec_raw, sub_raw, land_raw):
    """
    回傳 (action, new_section, new_sub, flag)
    action: 'A_bracket' | 'A_mixed' | 'B_notfound' | 'B_landno' | 'ok'
    """
    city = norm_city(city_raw or '')
    sec  = str(sec_raw or '').strip()
    sub  = str(sub_raw or '').strip()
    land = str(land_raw or '').strip()

    if not land or land.lower() in ('nan', 'none', ''):
        return 'B_landno', sec, sub, '地號格式異常'

    m = MIXED_RE.match(sec)
    if m:
        return 'A_mixed', m.group(1), m.group(2), ''

    m = BRACKET_RE.match(sec)
    if m:
        clean = m.group(1).strip()
        if (city, clean) in valid_set or (city, clean.rstrip('段')) in valid_set:
            # 若小段與地段完全相同（含括號），視為誤填重複 → 清空小段
            new_sub = '' if sub == sec else sub
            return 'A_bracket', clean, new_sub, ''
        return 'B_notfound', sec, sub, f'查無官方段名（去括號後：{clean}）'

    if sec and (city, sec) not in valid_set and (city, sec.rstrip('段')) not in valid_set:
        return 'B_notfound', sec, sub, '查無官方段名'

    return 'ok', sec, sub, ''

# ── 讀取 SQLite 全表 ────────────────────────────────────────────
print("讀取 SQLite…")
t0 = time.time()
conn = sqlite3.connect(str(DB_PATH))

# 確認 sys_judgment 欄位是否存在
cur = conn.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(land_master)").fetchall()]
has_sys_judgment = 'sys_judgment' in cols
print(f"  sys_judgment 欄位：{'已存在' if has_sys_judgment else '不存在（需新增）'}")

df = pd.read_sql("SELECT id, city, section_raw, sub_section, land_no_raw FROM land_master", conn)
conn.close()
print(f"  {len(df):,} 筆  ({time.time()-t0:.1f}s)")

# ── classify 全表 ────────────────────────────────────────────────
print("classify 全表…")
t1 = time.time()

actions, new_secs, new_subs, flags = [], [], [], []
for _, row in df.iterrows():
    act, ns, nb, fl = classify(row['city'], row['section_raw'], row['sub_section'], row['land_no_raw'])
    actions.append(act)
    new_secs.append(ns)
    new_subs.append(nb)
    flags.append(fl)

df['_action']   = actions
df['_new_sec']  = new_secs
df['_new_sub']  = new_subs
df['_flag']     = flags
print(f"  classify 完成  {time.time()-t1:.1f}s")

# ── 統計 ─────────────────────────────────────────────────────────
from collections import Counter
counts = Counter(actions)

df_A = df[df['_action'].str.startswith('A_')].copy()
df_B = df[df['_action'].str.startswith('B_')].copy()

# 小段被清空的筆數（sub_section 原有值，新值為空）
sub_cleared = df_A[(df_A['sub_section'].str.strip() != '') & (df_A['_new_sub'] == '')].shape[0]

print()
print("=== DRY-RUN 統計 ===")
print(f"  ok（無需修正）  : {counts.get('ok', 0):>7,}")
print(f"  A_bracket       : {counts.get('A_bracket', 0):>7,}")
print(f"  A_mixed         : {counts.get('A_mixed', 0):>7,}")
print(f"  B_notfound      : {counts.get('B_notfound', 0):>7,}")
print(f"  B_landno        : {counts.get('B_landno', 0):>7,}")
print(f"  ────────────────────────")
print(f"  A類合計（預計 UPDATE section_raw）: {len(df_A):,}")
print(f"  其中小段清空筆數（sub==section 誤填）: {sub_cleared:,}")
print(f"  B類合計（預計 UPDATE sys_judgment）: {len(df_B):,}")
print()

# A類 sample
if len(df_A):
    print("--- A類 sample（前5筆）---")
    for _, r in df_A.head(5).iterrows():
        sub_note = f"→ 『清空』" if (r['sub_section'] and r['_new_sub'] == '') else f"→ {r['_new_sub']!r}"
        print(f"  id={r['id']}  [{r['_action']}]  地段: {r['section_raw']} → {r['_new_sec']}  小段: {r['sub_section']!r} {sub_note}")

# B類 sample
if len(df_B):
    print()
    print("--- B類 sample（前5筆）---")
    for _, r in df_B.head(5).iterrows():
        print(f"  id={r['id']}  [{r['_action']}]  {r['section_raw']}  flag={r['_flag']}")

print()
print(f"  sys_judgment 欄位：{'已存在，無需 ALTER' if has_sys_judgment else '需 ALTER TABLE 新增'}")
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

# ── 備份 SQLite ─────────────────────────────────────────────────
print(f"\n備份 SQLite → {DB_BACKUP}")
shutil.copy2(DB_PATH, DB_BACKUP)
print(f"  備份完成（{DB_BACKUP.stat().st_size/1024/1024:.1f} MB）")

# ── ALTER TABLE 新增 sys_judgment ────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))
cur  = conn.cursor()

if not has_sys_judgment:
    print("ALTER TABLE 新增 sys_judgment…")
    cur.execute("ALTER TABLE land_master ADD COLUMN sys_judgment TEXT")
    conn.commit()
    print("  完成")

# ── UPDATE A類：section_raw + sub_section ────────────────────────
print(f"\nUPDATE A類 {len(df_A):,} 筆…")
t2 = time.time()
a_updated = 0
for _, r in df_A.iterrows():
    cur.execute(
        "UPDATE land_master SET section_raw=?, sub_section=? WHERE id=?",
        (r['_new_sec'] or None, r['_new_sub'] or None, int(r['id']))
    )
    a_updated += cur.rowcount
conn.commit()
print(f"  完成 {a_updated:,} 行  ({time.time()-t2:.1f}s)")

# ── UPDATE B類：sys_judgment ─────────────────────────────────────
print(f"\nUPDATE B類 {len(df_B):,} 筆…")
t3 = time.time()
b_updated = 0
for _, r in df_B.iterrows():
    cur.execute(
        "UPDATE land_master SET sys_judgment=? WHERE id=?",
        (r['_flag'], int(r['id']))
    )
    b_updated += cur.rowcount
conn.commit()
conn.close()
print(f"  完成 {b_updated:,} 行  ({time.time()-t3:.1f}s)")

# ── 輸出 change_log ──────────────────────────────────────────────
df_log = pd.concat([
    df_A[['id', '_action', 'city', 'section_raw', '_new_sec', 'sub_section', '_new_sub']].rename(columns={
        '_action': 'action', '_new_sec': 'new_section', '_new_sub': 'new_sub_section'
    }),
    df_B[['id', '_action', 'city', 'section_raw', '_flag']].rename(columns={
        '_action': 'action', '_flag': 'sys_judgment'
    }),
], ignore_index=True)
df_log.to_csv(LOG_PATH, index=False, encoding='utf-8-sig')
print(f"\nchange_log：{len(df_log):,} 筆 → {LOG_PATH}")

# ── 匯出 Excel 檢視檔 ────────────────────────────────────────────
print(f"\n匯出 Excel 檢視檔…")
t4 = time.time()
conn2 = sqlite3.connect(str(DB_PATH))

EXPORT_COLS_SQL = """
SELECT
    sys_judgment AS 系統判定,
    updated_at   AS 更新日期,
    zone_type    AS 分區,
    location_tag AS 位置,
    city         AS 縣市,
    district     AS 地區,
    section_raw  AS 地段,
    sub_section  AS 小段,
    land_no_raw  AS 地號,
    announced_value AS 公告現值,
    reg_seq      AS 次序,
    reg_date     AS 登記日期,
    reg_reason   AS 登記原因,
    cause_date   AS 發生日期,
    owner_name   AS 所有權人,
    owner_id_full AS 統一編號（完整）,
    postal_code  AS 郵遞區號,
    address      AS 住址,
    is_sold      AS 已售出,
    share_denom  AS 分母,
    share_numer  AS 分子,
    purchase_price AS 持分,
    actual_owned_area AS 持分坪數,
    total_area_ping AS 土地總坪數,
    note         AS 備註,
    phone        AS 電話
FROM land_master
ORDER BY id
"""
df_export = pd.read_sql(EXPORT_COLS_SQL, conn2)
conn2.close()

df_export.to_excel(EXCEL_OUT, index=False, engine='openpyxl')
print(f"  匯出完成：{len(df_export):,} 行  ({time.time()-t4:.1f}s)")
print(f"  路徑：{EXCEL_OUT}")

print(f"\n總耗時：{time.time()-t0:.1f}s")
print("完成。")
