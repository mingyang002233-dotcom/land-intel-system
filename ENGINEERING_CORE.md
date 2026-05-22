# ENGINEERING CORE

本專案需搭配：

老蕭LAND_AI協作核心架構_v4.pdf

一起閱讀。

---

# 專案定位

此專案專注於：

- 實價登錄
- 土地情報
- SQLite
- Telegram Bot
- n8n workflow
- AI 自動化
- 自然語查詢

---

# 核心原則

1. 避免高額 API 消耗

2. 優先低成本長期運行

3. 優先本機 SQLite 架構

4. 原始資料禁止刪除

5. Query 階段才篩選資料

6. 不要過度複雜化 workflow

7. 優先穩定、可維護、可長期運作

---

# AI 分工

GPT：
負責分析、規劃、架構。

Claude：
負責 Code、Workflow、API、自動化。

n8n：
負責執行 workflow 與通知。

---

# 開發原則

- 先規劃再執行
- 避免反覆重跑浪費 API
- 修改前先確認流程
- 優先 MVP
- 禁止破壞原始資料