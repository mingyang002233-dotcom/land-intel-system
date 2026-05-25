"""
match_realprice.py
實價登錄 land_transactions → land_master 比對核心。

比對鍵：縣市 + 區域 + 地段 + 地號（四鍵全比對）
資料來源：
  land_master  → data/database/land_master.db
  land_transactions → db/land_intel.db

執行模式：
  python match_realprice.py          → dry-run（預設，不動 SQLite）
  python match_realprice.py --apply  → 回寫 realprice_match_status（待後續開放）

dry-run 輸出：
  output/realprice_match_dryrun_YYYYMMDD.csv

normalize 規則（只用於比對，不寫回）：
  city   → 不硬轉台/臺，從兩個 DB 實際值建立 alias
  地段   → 去括號代碼後比對（蘆興段(0834) → 蘆興段）
  地號   → 正規化為 ####-#### 格式（834 → 0834-0000, 834-1 → 0834-0001）
"""

import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

LAND_DB  = Path("data/database/land_master.db")
PRICE_DB = Path("db/land_intel.db")
LOG_DIR  = Path("/Users/xiaomingyang/Desktop/excel土地資料維護/output")
LOG_DIR.mkdir(parents=True, exist_ok=True)

APPLY_MODE = '--apply' in sys.argv
TIMESTAMP  = datetime.now().strftime('%Y%m%d')
OUT_CSV    = LOG_DIR / f"realprice_match_dryrun_{TIMESTAMP}.csv"

BRACKET_RE = re.compile(r'^(.+?)\s*\(\d{4}\)$')

# ── normalize 函式 ───────────────────────────────────────────────

def strip_bracket(s):
    m = BRACKET_RE.match(str(s or '').strip())
    return m.group(1).strip() if m else str(s or '').strip()

def norm_land_no(s):
    """地號正規化 → ####-#### 格式"""
    s = str(s or '').strip()
    if not s or s.lower() in ('nan', 'none', ''): return None
    # 已是 ####-#### 格式
    m = re.match(r'^(\d{4})-(\d{4})$', s)
    if m: return s
    # ####-# 或 ####-## 等（補零到4位）
    m = re.match(r'^(\d+)-(\d+)$', s)
    if m: return f"{int(m.group(1)):04d}-{int(m.group(2)):04d}"
    # 純數字（無連字號）→ ####-0000
    m = re.match(r'^(\d+)$', s)
    if m: return f"{int(m.group(1)):04d}-0000"
    return s  # 無法處理，原值

def norm_city(s, alias_map):
    s = str(s or '').strip()
    return alias_map.get(s, s)

def norm_section(s):
    return strip_bracket(str(s or '').strip())

# ── 建立 city alias（從兩個 DB 的實際值）────────────────────────
print("建立 city alias…")
lm_conn = sqlite3.connect(str(LAND_DB))
rp_conn = sqlite3.connect(str(PRICE_DB))

lm_cities = set(pd.read_sql("SELECT DISTINCT city FROM land_master WHERE city IS NOT NULL", lm_conn)['city'])
rp_cities = set(pd.read_sql("SELECT DISTINCT city FROM land_transactions WHERE city IS NOT NULL", rp_conn)['city'])

city_alias = {}  # 任意寫法 → 統一寫法（以 land_master 為基準）
for c in lm_cities:
    city_alias[c] = c
    alt = c.replace('臺','台') if '臺' in c else c.replace('台','臺')
    if alt in rp_cities and alt not in city_alias:
        city_alias[alt] = c  # 實價寫法 → land_master 寫法

print(f"  land_master 城市：{sorted(lm_cities)}")
print(f"  land_transactions 城市：{sorted(rp_cities)}")
print(f"  alias 對照（實價→主清冊）：")
for k, v in sorted(city_alias.items()):
    if k != v: print(f"    {k!r} → {v!r}")

# ── 讀取 land_master（只取比對所需欄位）────────────────────────
print("\n讀取 land_master…")
df_lm = pd.read_sql("""
    SELECT id, city, district, section_raw, land_no_raw,
           owner_name, reg_date, is_sold, realprice_match_status
    FROM land_master
""", lm_conn)
lm_conn.close()
print(f"  {len(df_lm):,} 筆")

# normalize land_master 比對鍵
df_lm['_city']    = df_lm['city'].apply(lambda x: norm_city(x, city_alias))
df_lm['_section'] = df_lm['section_raw'].apply(norm_section)
df_lm['_land_no'] = df_lm['land_no_raw'].apply(norm_land_no)
df_lm['_key']     = (df_lm['_city'] + '|' + df_lm['district'].fillna('') + '|' +
                     df_lm['_section'] + '|' + df_lm['_land_no'].fillna(''))

# ── 讀取 land_transactions（只取土地相關交易）──────────────────
print("讀取 land_transactions…")
df_rp = pd.read_sql("""
    SELECT id, city, district, section_name, land_number,
           trade_date, total_price_wan, unit_price_per_sqm,
           transaction_target, source_kind
    FROM land_transactions
    WHERE section_name IS NOT NULL
      AND land_number IS NOT NULL
      AND TRIM(section_name) != ''
      AND TRIM(land_number) != ''
""", rp_conn)
rp_conn.close()
print(f"  有效筆數（section+land_number 非空）：{len(df_rp):,}")

# normalize 實價比對鍵
df_rp['_city']    = df_rp['city'].apply(lambda x: norm_city(x, city_alias))
df_rp['_section'] = df_rp['section_name'].apply(norm_section)
df_rp['_land_no'] = df_rp['land_number'].apply(norm_land_no)
df_rp['_key']     = (df_rp['_city'] + '|' + df_rp['district'].fillna('') + '|' +
                     df_rp['_section'] + '|' + df_rp['_land_no'].fillna(''))

# ── 比對 ─────────────────────────────────────────────────────────
print("\n執行比對…")
rp_key_set = set(df_rp['_key'])

df_lm['_matched'] = df_lm['_key'].isin(rp_key_set)
df_hit = df_lm[df_lm['_matched']].copy()
df_miss = df_lm[~df_lm['_matched']].copy()

# 對每筆命中，取對應的實價記錄（可能多筆）
rp_by_key = df_rp.groupby('_key')

rows_out = []
for _, lm_row in df_hit.iterrows():
    rp_rows = rp_by_key.get_group(lm_row['_key']) if lm_row['_key'] in rp_by_key.groups else pd.DataFrame()
    for _, rp_row in rp_rows.iterrows():
        rows_out.append({
            '縣市':       lm_row['city'],
            '區域':       lm_row['district'],
            '地段':       lm_row['section_raw'],
            '地號':       lm_row['land_no_raw'],
            'land_master_id': lm_row['id'],
            '所有權人':   lm_row['owner_name'],
            '登記日期':   lm_row['reg_date'],
            '實價交易日期': rp_row['trade_date'],
            '實價總價(萬)': rp_row['total_price_wan'],
            '實價單價(元/㎡)': rp_row['unit_price_per_sqm'],
            '交易標的':   rp_row['transaction_target'],
            'match_key':  lm_row['_key'],
            'match_status': 'hit',
        })

df_out = pd.DataFrame(rows_out)

# ── 同批交易檢查（只限已命中 land_master 的實價記錄）──────────
hit_key_set = set(df_hit['_key'])
df_rp_hit = df_rp[df_rp['_key'].isin(hit_key_set)]
batch_key = df_rp_hit['_city'] + '|' + df_rp_hit['district'].fillna('') + '|' + df_rp_hit['_section'] + '|' + df_rp_hit['trade_date'].astype(str)
batch_counts = batch_key.value_counts()
batch_suspicious = batch_counts[batch_counts >= 3]  # 同段同日 3筆以上

# 命中區域排行
if len(df_hit):
    area_rank = (df_hit.groupby(['city', 'district', '_section']).size()
                 .reset_index(name='命中筆數')
                 .sort_values('命中筆數', ascending=False))

# ── 統計輸出 ─────────────────────────────────────────────────────
print()
print("=== DRY-RUN 比對結果 ===")
print(f"  land_transactions 總筆數   : {len(df_rp):,}")
print(f"  land_master 總筆數         : {len(df_lm):,}")
print(f"  land_master 命中筆數       : {len(df_hit):,}")
print(f"  land_master 未命中筆數     : {len(df_miss):,}")
print(f"  輸出 CSV 筆數（含多筆實價） : {len(df_out):,}")
print()
print(f"  同批交易疑似（同段同日 ≥3筆）: {len(batch_suspicious):,} 組")
if len(batch_suspicious):
    for key, cnt in batch_suspicious.head(5).items():
        print(f"    {key}  →  {cnt} 筆")

print()
print("── 命中區域排行（前10）──")
if len(df_hit):
    for _, r in area_rank.head(10).iterrows():
        print(f"  {r['city']} {r['district']} {r['_section']:<14} {r['命中筆數']:>4} 筆")

print()
print("── 抽樣 20 筆命中 ──")
sample = df_out.head(20)
for _, r in sample.iterrows():
    print(f"  id={r['land_master_id']:7}  {r['縣市']} {r['區域']} {r['地段']} {r['地號']}"
          f"  所有人={r['所有權人']}  實價日={r['實價交易日期']}  總價={r['實價總價(萬)']}萬")

# ── 輸出 CSV ─────────────────────────────────────────────────────
df_out.to_csv(OUT_CSV, index=False, encoding='utf-8-sig')
print()
print(f"dry-run CSV → {OUT_CSV}  ({len(df_out):,} 筆)")
print()
print("=== DRY-RUN 完成，未修改任何 SQLite ===")
if APPLY_MODE:
    print("[INFO] --apply 模式尚未開放，請確認 dry-run 結果後再啟用。")
