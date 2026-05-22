# 每月增量更新流程

## 更新時機

官方資料發布日：內政部實價登錄通常於每月 **1 號、11 號、21 號** 釋出新資料。

老蕭 LAND 系統執行日：每月 **2 號、12 號、22 號 08:30** 執行增量下載與匯入，故意比官方發布日晚一天，降低抓到空資料或未同步 CSV 的風險。

---

## 完整步驟

### Step 1 — 下載最新 CSV

前往內政部實價登錄批次資料下載頁面，下載最新一期所有縣市 CSV。

解壓後放入專屬子目錄，例如：

```
csv/115q2/
```

### Step 2 — 匯入

```bash
python3 scripts/parse_realprice.py csv/115q2
# 或使用 maintenance_pipeline（會自動掃設定檔指定目錄）
python3 scripts/maintenance_pipeline.py
```

### Step 3 — 驗證

```bash
python3 scripts/validate_land_data.py
```

確認：
- Parse 成功率 100%
- 真正重複匯入 0 筆
- 無異常日期新增

### Step 4 — 確認更新狀態

```bash
python3 scripts/check_update_schedule.py
```

輸出：
- DB 最後交易日期
- 最後匯入期別
- 是否需要再下載

### Step 5 — Telegram 通知（選擇性）

```bash
python3 scripts/telegram_query_bot.py
```

Bot 啟動後，在 Telegram 輸入查詢即可取得最新成交資料。

---

## 匯入安全性

- `parse_realprice.py` 支援重複執行：同一批 CSV 跑兩次不會產生重複資料
- 以 `unique_key`（業務欄位組合）做 UPSERT 防重
- 合法共有交易（同地號不同權利人）不會被過濾

---

## 資料目標

| 期別 | 狀態 |
|------|------|
| 113年（2024） | 補下載 |
| 114年（2025） | 補下載 |
| 115年（2026） | ✅ 已有 |

---

## 不做的事

- 全歷史十年資料
- AI 分析 / 異常判斷
- 591 爬蟲整合

*最後更新：2026-05-15*
