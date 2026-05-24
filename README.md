# 老蕭 Land Intel System

主動追蹤地主動態的土地情報系統。核心流程：foundi 謄本調閱 → YAML 自動解析 → SQLite + Excel 同步更新 → Telegram 推播。

## 快速開始

```bash
# 調閱謄本後下載 YAML，download_watcher 自動搬入 電傳解析/
# 執行 ingestion（自動同步 MASTER + 移除實價提醒報表已調閱列）
python3 scripts/process_land_transcripts.py

# 實價提醒回溯比對（bulk import 後使用）
python3 scripts/realprice_alert.py --reconcile

# 人工已確認（免調閱）：從提醒報表移除 + 寫 MASTER 備註
python3 scripts/realprice_alert.py --manual-confirm \
    --section "地段" --land-no "0000-0000" [--reason "原因"]

# 補標舊地主已售 + 重新格式化（bulk import 後使用）
python3 scripts/realprice_alert.py --reconcile-sold

# 查看背景服務狀態
launchctl list | grep landmaster
```

## 文件

詳細架構、Pipeline 流程、資料夾規則、欄位定義、業務規則，請見：

**[docs/PIPELINE_SOP.md](docs/PIPELINE_SOP.md)** — 正式 Pipeline SOP（v5.4）

## 主要 Scripts

| Script | 功能 |
|--------|------|
| `download_watcher.py` | 監控 ~/Downloads → 搬入 電傳解析/ |
| `process_land_transcripts.py` | foundi YAML ingestion 主流程 |
| `realprice_alert.py` | 實價登錄比對 + 提醒報表管理 |
| `import_land_master.py` | 批量匯入 Excel 到 SQLite |
| `land_master_bot.py` | Telegram bot（常駐） |
| `update_excel_realprice.py` | 主清冊加實價比對欄 |

## 正式資料檔案

| 檔案 | 說明 |
|------|------|
| `data/database/land_master.db` | SQLite 主資料庫 |
| `Desktop/excel土地資料維護/最新完成版/老蕭LAND_MASTER.xlsx` | Excel 主清冊（唯一正式主檔） |
| `Desktop/excel土地資料維護/最新完成版/實價提醒報表_最新完成版.xlsx` | 實價提醒報表 |
