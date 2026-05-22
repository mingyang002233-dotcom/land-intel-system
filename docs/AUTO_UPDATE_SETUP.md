# Mac 自動更新設定（launchd）

## 排程時機

老蕭 LAND 系統預定執行日：每月 **2 號、12 號、22 號 08:30**。

系統不要求 Mac 24 小時運行；若預定時間 Mac 沒開機、睡眠或網路不通，登入後會由 LaunchAgent 觸發補跑檢查，只在本期尚未成功時補跑一次。

內政部實價登錄官方資料發布日通常為每月 **1 號、11 號、21 號**。系統故意晚一天執行，避免官方資料延遲、CSV 尚未同步或凌晨資料不完整。

---

## 安裝 launchd

```bash
# 1. 載入排程（只需執行一次）
launchctl load ~/Library/LaunchAgents/com.laoxiao.land.update.plist

# 2. 確認已載入
launchctl list | grep laoxiao
# 應看到：com.laoxiao.land.update
```

---

## 手動立即觸發

```bash
# 方法 A：執行月更新入口（建議；已成功就不重複跑）
bash /Users/xiaomingyang/projects/land-intel-system/scripts/monthly_update.sh

# 方法 B：只檢查是否需要補跑，不執行月更新
python3 /Users/xiaomingyang/projects/land-intel-system/scripts/monthly_update_guard.py --status

# 方法 C：透過 launchctl 觸發（模擬排程或登入後觸發）
launchctl start com.laoxiao.land.update
```

---

## 查看 log

```bash
# 今日更新 log
cat logs/monthly_update_$(date +%Y%m%d).log

# 所有 log 清單
ls -lh logs/monthly_update_*.log

# launchd 輸出（stdout/stderr）
cat logs/launchd_stdout.log
cat logs/launchd_stderr.log

# 即時追蹤
tail -f logs/monthly_update_$(date +%Y%m%d).log
```

---

## 停止 / 重啟排程

```bash
# 停止
launchctl unload ~/Library/LaunchAgents/com.laoxiao.land.update.plist

# 重新啟用
launchctl load ~/Library/LaunchAgents/com.laoxiao.land.update.plist

# 修改 plist 後需 unload 再 load
launchctl unload ~/Library/LaunchAgents/com.laoxiao.land.update.plist
# 編輯 plist ...
launchctl load ~/Library/LaunchAgents/com.laoxiao.land.update.plist
```

---

## 更新流程說明

LaunchAgent 入口為 `monthly_update.sh`。腳本一開始會呼叫 `monthly_update_guard.py --status` 判斷本期是否已完成；若已完成會直接退出，若未完成才繼續月更新。

`monthly_update.sh` 依序執行：

| 步驟 | 指令 | 說明 |
|------|------|------|
| 1 | `download_realprice.py --period auto` | 下載官方最新期別（已下載自動跳過） |
| 2 | `maintenance_pipeline.py` | 解壓→parse→backfill→cleanup→validate |
| 3 | `notify_update.py` | 發送 Telegram 摘要通知 |
| 4 | `monthly_update_guard.py --mark-success` | 寫入本期成功標記，避免重複補跑 |

---

## 注意事項

- Mac 不需要長期不睡；錯過 2/12/22 08:30 時，開機/登入後由 `monthly_update.sh` 先檢查並補跑
- 若要手動檢查，可執行 `monthly_update_guard.py --status`；若要手動補跑，執行 `monthly_update.sh`
- 已匯入資料不會重複增加（`unique_key` 防重）
- 同一執行窗口完成後會寫入 `update_history.action_required = monthly_update_success:YYYY-MM-DD`，避免同期重複跑
- log 檔以日期命名，不會覆蓋舊 log
- 若 `logs/launchd_stderr.log` 出現 `Operation not permitted`，代表 macOS 擋住 launchd 讀取 Desktop 內的專案檔案；需將執行 shell 加入 Full Disk Access，或把專案移到非 Desktop/Documents/Downloads 的工作目錄。

---

## 日期定義

| 類型 | 日期 | 用途 |
|------|------|------|
| 官方資料發布日 | 每月 1 / 11 / 21 | 內政部可能釋出新實價登錄資料 |
| 老蕭 LAND 系統執行日 | 每月 2 / 12 / 22 08:30 | `monthly_update.sh` 先檢查本期是否需要執行；必要時才繼續月更新 |

---

## 相關檔案

| 檔案 | 說明 |
|------|------|
| `~/Library/LaunchAgents/com.laoxiao.land.update.plist` | launchd 排程定義 |
| `scripts/monthly_update_guard.py` | 判斷本期是否已成功、寫入成功標記 |
| `scripts/monthly_update.sh` | 開機/登入、排程與手動月更新入口；已完成則跳過 |
| `scripts/download_realprice.py` | CSV 下載器 |
| `scripts/maintenance_pipeline.py` | 維護 pipeline |
| `scripts/notify_update.py` | Telegram 通知 |
| `logs/monthly_update_YYYYMMDD.log` | 每次更新 log |

*最後更新：2026-05-16*
