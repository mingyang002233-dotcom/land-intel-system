# Land Intel System — 正式 Pipeline SOP

> 版本：v5.3（2026-05-24）  
> 此文件為 auto-compact / 新對話重啟後的「制度化記憶」。  
> 讀完此文件即可理解系統現況，無需翻歷史對話。

---

## 一、系統定位

老蕭土地情報系統的核心任務是：

1. **主動追蹤地主動態**：誰買了、誰賣了、誰還在
2. **實價訊號比對**：實價登錄出現 → 可能有地主異動 → 調閱謄本確認
3. **電傳自動解析**：foundi YAML 調閱謄本 → 自動更新 SQLite + MASTER Excel
4. **清冊視覺化**：已售灰色、近期買賣黃色，一眼看出生命週期

---

## 二、正式 Pipeline（端對端流程）

```
foundi 網站調閱謄本
    ↓ 下載 *.yaml.txt 到 ~/Downloads
    ↓
download_watcher.py（常駐 daemon）
    監控 ~/Downloads
    自動搬移 YAML 到 電傳解析/
    ↓
電傳解析/（inbox）
    ↓
process_land_transcripts.py
    解析 foundi YAML
    → 寫入 SQLite land_master.db
    → 同步更新 老蕭LAND_MASTER.xlsx
    → 標記已售 / 插入新地主
    → 全表重新排序 + 視覺格式化
    → 更新 實價提醒報表（已調閱）
    → Telegram 推播摘要
    → 移檔到 電傳已完成/
```

---

## 三、資料夾規則（正式鎖定）

### 電傳工作區

| 資料夾 | 路徑 | 用途 |
|--------|------|------|
| `電傳解析/` | `PROJECT_ROOT/電傳解析/` | **inbox**：watcher 搬入，ingestion 讀取 |
| `電傳已完成/` | `Desktop/excel土地資料維護/電傳已完成/` | 成功解析後移入 |
| `電傳錯誤/` | `Desktop/excel土地資料維護/電傳錯誤/` | 解析失敗移入 + `.error.log` |
| ~~電傳待解析/~~ | — | **已廢棄**，改用 `電傳解析/` |

### Excel 工作區（最新完成版/）

| 檔案 | 說明 |
|------|------|
| `老蕭LAND_MASTER.xlsx` | **正式主檔（唯一）**，禁止改名、禁止 timestamp |
| `老蕭LAND_MASTER_TEST.xlsx` | 測試用，永久保留，與正式完全分離 |
| `實價提醒報表_最新完成版.xlsx` | 最新實價提醒清單（realprice_alert.py 覆寫）|
| `backup/` | 所有備份均在此，script 自動產生 |

**禁止出現**：`_完成版.xlsx`、`_最新.xlsx`、`_copy.xlsx`、`timestamp.xlsx` 等自由命名。

---

## 四、正式 MASTER 清冊規則

### 欄位結構（32 欄）

欄 1–27：業務資料（更新日期、分區、位置、縣市、地區、地段、小段、地號…）  
欄 28–32（AB–AF）：系統判定欄位

| 欄 | 名稱 | 說明 |
|----|------|------|
| AB | 系統處理狀態 | 正常 / 待人工確認 / 舊電傳補歷史 / force匯入 等 |
| AC | 系統處理備註 | 自動產生的說明文字 |
| AD | 系統來源 | 電傳 / 手動 / 實價 / rebuild |
| AE | 系統更新時間 | 最後系統異動時間 |
| AF | 系統批次ID | `TXN_YYYYMMDD_HHMM_NNN` |

### 視覺格式規則（優先順序由高至低）

1. **已售出** → 整列灰字（`999999`）+ 淡灰底（`EBEBEB`）
2. **近半年買賣**（登記原因=買賣，登記日距今 ≤ 180 天，且未售出）→ 淡黃底（`FFFACD`）
3. 其他 → 正常格式

格式在每次 `process_land_transcripts.py` 執行後自動重跑 `reformat_and_sort_master()`。

### 排序規則

地段 → 地號（normalized）→ 登記日期（民國升序）→ 登記次序

### 資料保留原則

- **不刪歷史資料**：已售出的舊地主保留、只反灰
- 同地號生命週期完整呈現：舊地主（灰）→ 新地主（白/黃）

---

## 五、event_key 規則

```
event_key = SHA256(
    land_match_key | owner_key | reg_seq | reg_date | reg_reason | share_numer | share_denom
)[:16]
```

- 唯一識別一筆登記事件
- 相同事件重複解析 → SKIP（不重複寫入）
- 同一地主同一地號不同時期的歷史登記 → 不同 event_key

---

## 六、batch_id 規則

格式：`TXN_YYYYMMDD_HHMM_NNN`

- `NNN`：同一次執行內的檔案序號（001, 002, …）
- 每筆 SQLite 記錄和 Excel 系統欄位都帶 batch_id
- 用於追蹤「這筆資料是哪次解析產生的」

---

## 七、sys_status 值定義

| 值 | 觸發條件 | Excel 行為 |
|----|----------|-----------|
| `正常` | 新電傳，yaml 日期 > DB 最新日期 | 完整 diff + 標已售 |
| `待人工確認` | 同日事件（yaml 日期 = DB 最新日期）| Excel 仍插入新地主，**不**標已售 |
| `舊電傳補歷史` | yaml 日期 < DB 最新日期 | **只**寫 SQLite，**不**更新 Excel |
| `force匯入` | --force 旗標強制 | 寫入但不標已售（--force 不等於 --force-sold）|

---

## 八、Excel Sync 規則（process_land_transcripts.py）

```
對每個地號：

  若 sys_status='舊電傳補歷史' → 跳過 Excel（完全不改）
  
  若 sys_status='待人工確認' → 做 diff，可插入新地主，但不標已售
  
  若 sys_status='正常'（新電傳）→ 完整 diff：
    消失的地主 → 標已售 + 整列反灰
    新增的地主 → 插入在同地號最後一列下方
    持分改變   → 備註追加
    未變動     → 不動
```

**allow_sold_mark 保守模式**：  
只有 `rec.get('allow_sold_mark') is True` 才進入已售流程。  
`False / None / 缺失` 一律不得標已售。

---

## 九、實價提醒報表核心規則（正式鎖定 v5.3）

### 定位

**實價提醒報表 = 尚未調閱的待辦清單**，只保留現在仍需調閱謄本的地號。  
歷史資料（已調閱、已反映）保存於 SQLite + MASTER 清冊，**不留在報表中**。

### 觸發條件（會進入報表）

- 實價登錄出現某地號的買賣交易
- MASTER 目前無對應的新地主登記

### 不觸發條件（不進入報表）

- 增貸、抵押設定、他項設定
- 繼承、贈與、地目調整、分割、合併
- → 這些屬於未來「他項權利情報」或「地主金融壓力」模組

### 移除條件（從報表刪除）

以下情況直接 **刪除該列**，不留任何標記狀態：

1. foundi 電傳成功解析（`reg_reason='買賣'`）→ `mark_realprice_processed()` 刪除
2. `reconcile_realprice_alerts()` 判定「已反映」→ 直接刪除
3. `reconcile_realprice_alerts()` 遇到舊有「已調閱」殘留列 → 一併清除

### is_reflected_in_master() 核心邏輯

```
「已反映」= 同地號在 DB 已存在：
  reg_reason = '買賣'
  reg_date   >= 實價成交日期（差距 ≤ 90 天）

→ 代表新地主結構已存在，此地號直接從報表移除
```

**明確不使用**：`is_sold` / 已售出欄位  
理由：舊清冊歷史資料很多缺乏完整已售標記。  
「新地主是否存在」與「舊地主是否標已售」是兩件獨立的事。

### mark_realprice_processed() 觸發條件

1. `reg_reason = '買賣'`（電傳確認買賣事件）
2. 成功寫入 SQLite
3. 報表有該地號的列

觸發後：直接 `delete_rows`，不寫「已調閱」欄位。  
非買賣事件（抵押、地目等）**不**觸發，即使電傳解析成功。

### reconcile_realprice_alerts() 用途

批量匯入（`import_land_master.py`）後，報表不會自動更新。  
執行 `--reconcile` 回溯比對整張報表，移除已反映地號：

```bash
python3 scripts/realprice_alert.py --reconcile --dry-run  # 預覽（顯示將移除幾列）
python3 scripts/realprice_alert.py --reconcile            # 正式執行（直接刪列）
```

### reconcile_sold_status() 用途

bulk import 歷史資料未跑 diff，導致舊地主 `is_sold=0`、Excel 未反灰。  
執行 `--reconcile-sold` 補標已售並重新格式化：

```bash
python3 scripts/realprice_alert.py --reconcile-sold --dry-run  # 預覽
python3 scripts/realprice_alert.py --reconcile-sold            # 正式執行 + reformat
```

**判定規則**：`reg_reason ≠ '買賣'`（前手）且 `reg_date < 同地號最早買賣日期` → 標 `is_sold=1`。  
`reg_reason='買賣'` 的記錄**永不自動標售**（買賣本身即為現任或歷史購入方）。

---

## 十、download_watcher.py 設定

```
監控：~/Downloads
目的：PROJECT_ROOT/電傳解析/
副檔名：*.yaml  *.yml  *.yaml.txt  *.yaml.txt.yml
衝突：加 HHMMSSff timestamp 後綴，不覆蓋
日誌：logs/download_watcher.log
```

**LaunchAgent 管理**（macOS 背景常駐）：

```bash
# 啟動
launchctl load ~/Library/LaunchAgents/com.landmaster.download-watcher.plist

# 確認
launchctl list | grep landmaster

# 停止
launchctl unload ~/Library/LaunchAgents/com.landmaster.download-watcher.plist
```

---

## 十一、完整 Production Workflow（每次調閱謄本）

```
1. 到 foundi 調閱謄本 → 下載 YAML 到 ~/Downloads
2. download_watcher 自動搬到 電傳解析/（watcher 常駐時自動完成）
3. 執行 ingestion：
     python3 scripts/process_land_transcripts.py
4. 系統自動：
     - 解析 YAML
     - 寫入 SQLite（新事件）/ SKIP（重複事件）
     - 比對 MASTER Excel（diff 地主）
     - 標已售 / 插入新地主 / 備註持分變化
     - 全表排序 + 重新上色（約 90 秒）
     - 更新 實價提醒報表 已調閱欄
     - Telegram 推播摘要
     - 移檔到 電傳已完成/
5. 確認 Telegram 推播內容
6. 若有 sys_status='待人工確認'，人工核查後手動更新 SQLite + Excel
```

---

## 十二、主要 Scripts 對照表

| Script | 功能 | 常用指令 |
|--------|------|---------|
| `download_watcher.py` | 監控 Downloads → 搬到 電傳解析/ | `--once` 只掃一次 |
| `process_land_transcripts.py` | YAML ingestion 主流程 | `--dry-run` 預覽 |
| `realprice_alert.py` | 實價登錄比對 + 提醒報表 | `--reconcile` 回溯比對 |
| `import_land_master.py` | 批量匯入 Excel 到 SQLite | `--file` 指定來源 |
| `land_master_bot.py` | Telegram bot（常駐）| LaunchAgent 管理 |
| `update_excel_realprice.py` | 主清冊加實價比對欄 | `--dry-run` |
| `reformat_and_sort_master()` | 全表格式化 + 排序（函數）| 由 process 自動呼叫 |
| `reconcile_realprice_alerts()` | 回溯比對，移除已反映地號（函數）| `--reconcile` CLI |
| `reconcile_sold_status()` | 補標 bulk import 舊地主 is_sold + reformat | `--reconcile-sold` CLI |

---

## 十三、TEST 環境

- `老蕭LAND_MASTER_TEST.xlsx`：永久保留，用於新功能驗收
- `data/database/land_master_test.db`：同上
- **不要**在 TEST 驗收前動正式檔案

---

## 十四、已知 TODO（未來建置）

| 項目 | 說明 | 優先度 |
|------|------|--------|
| `source_file` 欄位 | 記錄原始 YAML 檔名到 land_master 主表 | 中 |
| `warrant_no` 獨立欄位 | 權狀字號目前存在 note 欄，應獨立 | 低 |
| 他項權利情報模組 | 抵押設定 / 增貸壓力追蹤 | 未來 |
| 地主金融壓力模型 | 融資比例、貸款銀行追蹤 | 未來 |
| `--force-sold` 旗標 | 強制標已售（目前 `--force` 不允許標售）| 需要時再實作 |
| `前次移轉現值` 寫入 DB | 目前解析但未落欄 | 低 |
| reconcile 整合到 import_land_master.py | 批量匯入後自動 reconcile + reconcile-sold | 中 |

---

## 十五、資料庫結構摘要

### land_master（主表，45 欄）

核心欄位：`event_key`（唯一識別）、`land_match_key`、`owner_key`  
地號：`section_raw`、`normalized_section`、`land_no_raw`、`normalized_land_no`  
地主：`owner_name`、`owner_id_masked`、`owner_id_full`  
登記：`reg_date`、`reg_reason`、`reg_seq`、`share_numer`、`share_denom`  
狀態：`is_sold`  
系統：`sys_status`、`sys_note`、`sys_source`、`sys_updated_at`、`sys_batch_id`

### transcript_import_log

每次解析的執行紀錄：`batch_id`、`source_file`、`event_key`、`status`

---

*本文件應在每次架構重大變更後更新。*  
*上次更新：2026-05-24（v5.3）*
