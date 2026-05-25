# land-intel-system — Claude 工作規範

## DB 修改 Skill
任何涉及 SQLite DB 修改、location_tag 打標、migration、批次 UPDATE、資料備份的任務，
執行前必須讀取並遵守：

```
~/.codex/skills/land-db-safe-update/SKILL.md
```

重點：dry-run 先行、備份再寫、不覆蓋既有 tag、DB 不進 Git、migration script 要 commit。
