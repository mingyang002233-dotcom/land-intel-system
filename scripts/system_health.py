#!/usr/bin/env python3
"""
system_health.py
Read-only health check for the LAND intelligence system.

This script intentionally does not modify DB, CSV, logs, or config files.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "land_intel.db"
WATCHLIST_PATH = PROJECT_ROOT / "config" / "watchlist.yaml"
LOG_DIR = PROJECT_ROOT / "logs"
ENV_PATHS = [PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"]

REQUIRED_TABLES = [
    "land_transactions",
    "land_details",
    "build_details",
    "park_details",
    "import_logs",
    "update_history",
]

LISTING_TABLES = [
    "land_listings",
    "listing_snapshots",
]


@dataclass
class CheckResult:
    level: str
    title: str
    detail: str = ""


def icon(level: str) -> str:
    return {
        "ok": "✅",
        "warn": "⚠️",
        "error": "❌",
    }[level]


def add(results: list[CheckResult], level: str, title: str, detail: str = "") -> None:
    results.append(CheckResult(level, title, detail))


def load_dotenv_status(paths: list[Path]) -> tuple[Path | None, dict[str, str]]:
    env_values: dict[str, str] = {}
    found_path = None
    for path in paths:
        if not path.exists():
            continue
        found_path = path
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env_values[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:
            return found_path, env_values
        break

    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if os.environ.get(key):
            env_values[key] = os.environ[key]

    return found_path, env_values


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def count_table(conn: sqlite3.Connection, table_name: str) -> int | None:
    if not table_exists(conn, table_name):
        return None
    row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    return int(row[0])


def latest_monthly_log(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    logs = sorted(log_dir.glob("monthly_update_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    if PROJECT_ROOT.name == "land-intel-system" and (PROJECT_ROOT / "scripts").exists():
        add(results, "ok", "專案根目錄正確", str(PROJECT_ROOT))
    else:
        add(results, "error", "專案根目錄可能不正確", str(PROJECT_ROOT))

    if DB_PATH.exists():
        add(results, "ok", "db/land_intel.db 存在", str(DB_PATH))
    else:
        add(results, "error", "db/land_intel.db 不存在", str(DB_PATH))
        return results

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        add(results, "ok", "SQLite 可用唯讀模式連線")
    except sqlite3.Error as exc:
        add(results, "error", "SQLite 連線失敗", str(exc))
        return results

    try:
        for table in REQUIRED_TABLES:
            if table_exists(conn, table):
                add(results, "ok", f"資料表存在：{table}")
            else:
                add(results, "error", f"資料表不存在：{table}")

        land_count = count_table(conn, "land_transactions")
        if land_count is None:
            add(results, "error", "無法統計實價登錄主要資料表筆數")
        elif land_count > 0:
            add(results, "ok", "實價登錄主要資料表筆數", f"{land_count:,} 筆")
        else:
            add(results, "warn", "實價登錄主要資料表目前為 0 筆")

        for table in LISTING_TABLES:
            count = count_table(conn, table)
            if count is None:
                add(results, "warn", f"591 相關資料表不存在：{table}")
            else:
                level = "ok" if count > 0 else "warn"
                add(results, level, f"591 相關資料表筆數：{table}", f"{count:,} 筆")
    finally:
        conn.close()

    if WATCHLIST_PATH.exists():
        add(results, "ok", "config/watchlist.yaml 存在", str(WATCHLIST_PATH))
    else:
        add(results, "warn", "config/watchlist.yaml 不存在", str(WATCHLIST_PATH))

    if LOG_DIR.exists():
        add(results, "ok", "logs/ 目錄存在", str(LOG_DIR))
        latest_log = latest_monthly_log(LOG_DIR)
        if latest_log:
            add(results, "ok", "最近 monthly_update log 存在", latest_log.name)
        else:
            add(results, "warn", "找不到 monthly_update log")
    else:
        add(results, "warn", "logs/ 目錄不存在", str(LOG_DIR))

    env_path, env_values = load_dotenv_status(ENV_PATHS)
    if env_path:
        add(results, "ok", ".env 存在", str(env_path))
    else:
        add(results, "warn", ".env 不存在", "已檢查專案根目錄與上一層")

    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if env_values.get(key):
            add(results, "ok", f"{key} 已設定", "不顯示敏感內容")
        else:
            add(results, "warn", f"{key} 未設定")

    return results


def final_status(results: list[CheckResult]) -> tuple[str, str]:
    if any(r.level == "error" for r in results):
        return "有錯誤", "先修正 ❌ 錯誤項目，再執行月更新或 Telegram 推播。"
    if any(r.level == "warn" for r in results):
        return "有警告", "先確認 ⚠️ 警告項目是否符合預期；若是新環境，通常先補資料或建立快照。"
    return "正常", "可執行月更新流程，或依需求測試 591 摘要與實價雷達推播。"


def main() -> int:
    results = run_checks()
    print("LAND 系統健康檢查")
    print("=" * 40)
    for result in results:
        detail = f"：{result.detail}" if result.detail else ""
        print(f"{icon(result.level)} {result.title}{detail}")

    status, suggestion = final_status(results)
    print("=" * 40)
    print(f"系統狀態：{status}")
    print(f"建議下一步：{suggestion}")
    return 1 if status == "有錯誤" else 0


if __name__ == "__main__":
    raise SystemExit(main())
