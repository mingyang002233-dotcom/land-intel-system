#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
push_social_review.py — 推播社群內容到 Telegram 供審核

用法：
  python3 scripts/push_social_review.py                    # 推播今天
  python3 scripts/push_social_review.py --date 2026-05-19  # 指定日期
"""

import argparse
import json
import os
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT  = PROJECT_ROOT / 'outputs' / 'social'
REVIEW_ROOT  = OUTPUT_ROOT / 'review_queue'

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE


def _load_env() -> dict:
    env = {}
    for path in [PROJECT_ROOT / '.env', PROJECT_ROOT.parent / '.env']:
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k in ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


# ── Telegram API helpers ──────────────────────────────────

def _send_message(token: str, chat_id: str, text: str) -> bool:
    data = json.dumps({'chat_id': chat_id, 'text': text}).encode('utf-8')
    req  = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=data, headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=15) as r:
            return json.loads(r.read()).get('ok', False)
    except Exception as e:
        print(f'  [ERROR] sendMessage: {e}')
        return False


def _send_photo(token: str, chat_id: str, img_path: Path, caption: str = '') -> bool:
    """multipart/form-data 上傳圖片"""
    boundary = 'LandBoundary591'
    ctype    = 'image/png'

    img_bytes = img_path.read_bytes()
    body  = f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
    body += f'--{boundary}\r\n'.encode()
    if caption:
        cap_bytes = caption[:1024].encode('utf-8')
        body += f'Content-Disposition: form-data; name="caption"\r\n\r\n'.encode()
        body += cap_bytes + b'\r\n'
        body += f'--{boundary}\r\n'.encode()
    body += (f'Content-Disposition: form-data; name="photo"; filename="{img_path.name}"\r\n'
             f'Content-Type: {ctype}\r\n\r\n').encode()
    body += img_bytes
    body += f'\r\n--{boundary}--\r\n'.encode()

    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendPhoto',
        data=body,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as r:
            return json.loads(r.read()).get('ok', False)
    except Exception as e:
        print(f'  [ERROR] sendPhoto ({img_path.name}): {e}')
        return False


# ── 推播單則貼文 ──────────────────────────────────────────

def _push_one(token: str, chat_id: str, rank: int, folder: Path) -> bool:
    # 讀檔
    def _read(name: str) -> str:
        p = folder / name
        return p.read_text(encoding='utf-8').strip() if p.exists() else ''

    kw      = folder.name.split('_', 2)[-1]   # post_01_航空城 → 航空城
    preview_txt = _read('telegram_preview.txt')
    candidates_txt = _read('candidate_summary.txt')

    # ── 1. 標頭訊息 ──
    header = (
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'【今日新聞圖文 Review #{rank}】\n\n'
        f'資料夾：{kw}\n'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'只供審核，不會自動發布 FB / IG'
    )
    _send_message(token, chat_id, header)

    # ── 2. Telegram preview ──
    if preview_txt:
        _send_message(token, chat_id, preview_txt[:3900])

    # ── 3. 候選摘要 ──
    if candidates_txt:
        _send_message(token, chat_id, f'📋 候選摘要：\n\n{candidates_txt[:3500]}')

    print(f'  ✅ #{rank} {kw} 推播完成')
    return True


# ── 主流程 ────────────────────────────────────────────────

def push_review(target_date: date = None) -> bool:
    d   = target_date or date.today()
    out = REVIEW_ROOT / d.strftime('%Y-%m-%d')

    if not out.exists():
        print(f'[ERROR] 找不到輸出資料夾：{out}')
        print('請先執行 python3 scripts/social_generator.py')
        return False

    folders = sorted(out.glob('post_*'))
    if not folders:
        print(f'[ERROR] {out} 中沒有貼文資料夾')
        return False

    env     = _load_env()
    token   = env.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = env.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat_id:
        print('[ERROR] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
        return False

    # 總覽訊息
    overview = (
        f'🏭 老蕭 LAND AI 內容工廠 v1\n'
        f'📅 {d.strftime("%Y/%m/%d")}\n\n'
        f'今日主文：1 則\n'
        f'另附候選摘要供審核。\n'
        f'不會自動發布 FB / IG。'
    )
    _send_message(token, chat_id, overview)

    for rank, folder in enumerate(folders[:1], 1):
        print(f'\n推播第 {rank} 則：{folder.name}')
        _push_one(token, chat_id, rank, folder)

    # 結尾
    _send_message(token, chat_id,
        '✅ Review preview 推送完成\n\n確認內容後再手動決定是否發布。')

    print('\n✅ 所有推播完成')
    return True


def main():
    parser = argparse.ArgumentParser(description='推播社群審核內容到 Telegram')
    parser.add_argument('--date', default=None, help='指定日期 YYYY-MM-DD')
    args   = parser.parse_args()
    target = date.fromisoformat(args.date) if args.date else None
    push_review(target_date=target)


if __name__ == '__main__':
    main()
