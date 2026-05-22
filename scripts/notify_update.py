#!/usr/bin/env python3
"""
notify_update.py
月更新完成後發送 Telegram 摘要通知。
由 monthly_update.sh 呼叫，也可單獨執行。
"""
import json
import argparse
import re
import sqlite3
import ssl
import sys
import urllib.request
import urllib.parse
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
ENV_PATH = PROJECT_ROOT / '.env'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def load_env() -> dict[str, str]:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


def get_db_stats() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    total = conn.execute('SELECT COUNT(*) FROM land_transactions').fetchone()[0]
    max_date = conn.execute(
        "SELECT MAX(trade_date) FROM land_transactions "
        "WHERE trade_date BETWEEN '1990-01-01' AND '2100-01-01'"
    ).fetchone()[0]
    today = date.today()
    year_rows = {}
    for offset in range(3):
        w = today.year - offset
        cnt = conn.execute(
            "SELECT COUNT(*) FROM land_transactions "
            "WHERE trade_date BETWEEN ? AND ?",
            (f'{w}-01-01', f'{w}-12-31')
        ).fetchone()[0]
        year_rows[w - 1911] = cnt
    conn.close()
    return {'total': total, 'max_date': max_date, 'year_rows': year_rows}


def get_validate_warnings() -> list[str]:
    """從最新 log 檔撈 validate 結果，找出警告行。"""
    log_dir = PROJECT_ROOT / 'logs'
    today_str = date.today().strftime('%Y%m%d')
    log_path = log_dir / f'monthly_update_{today_str}.log'
    if not log_path.exists():
        return []
    text = log_path.read_text(errors='replace')
    warnings = []
    for line in text.splitlines():
        if line.startswith('  [警告]') or line.startswith('  [嚴重]'):
            warnings.append(line.strip())
    return warnings


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': text}).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage', data=data
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            result = json.loads(r.read())
            return result.get('ok', False)
    except Exception as e:
        print(f'Telegram 發送失敗：{e}')
        return False


def tail_log(log_path: str, max_lines: int = 20) -> str:
    path = Path(log_path)
    if not path.exists():
        return f'找不到 log：{log_path}'
    lines = path.read_text(errors='replace').splitlines()
    tail = '\n'.join(lines[-max_lines:])
    return tail[-2500:]


def send_failure_notification(token: str, chat_id: str, log_path: str) -> bool:
    today = date.today()
    roc = today.year - 1911
    msg = f"""❌ LAND 月更新失敗 — {roc}年{today.month}月{today.day}日

log：
{log_path}

最後 log：
{tail_log(log_path)}"""
    return send_telegram(token, chat_id, msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--failure', help='Send failure notification with the specified log file.')
    args = parser.parse_args()

    env = load_env()
    token = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')

    if not token or not chat_id:
        print('未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過通知')
        sys.exit(0)

    if args.failure:
        ok = send_failure_notification(token, chat_id, args.failure)
        print('Telegram 失敗通知 OK' if ok else 'Telegram 失敗通知 FAIL')
        sys.exit(0 if ok else 1)

    stats = get_db_stats()
    warnings = get_validate_warnings()
    today = date.today()
    roc = today.year - 1911

    year_lines = '\n'.join(
        f'  {ry}年（{ry+1911}）: {cnt:,} 筆'
        for ry, cnt in sorted(stats['year_rows'].items(), reverse=True)
    )

    warn_section = ''
    if warnings:
        warn_section = '\n⚠️ 待確認：\n' + '\n'.join(f'  {w}' for w in warnings)
    else:
        warn_section = '\n✅ 無待處理警告'

    msg = f"""📦 LAND 月更新 — {roc}年{today.month}月{today.day}日

DB 總筆數：{stats['total']:,}
最新交易日期：{stats['max_date']}

近三年各年筆數：
{year_lines}
{warn_section}

下次窗口：每月 2 / 12 / 22 號 08:30 自動執行"""

    ok = send_telegram(token, chat_id, msg)
    print('Telegram 通知 OK' if ok else 'Telegram 通知 FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
