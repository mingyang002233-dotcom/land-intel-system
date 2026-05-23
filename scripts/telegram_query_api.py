#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
LOG_PATH = PROJECT_ROOT / 'logs' / 'query_parser_debug.log'

sys.path.insert(0, str(SCRIPT_ROOT))
from query_land import (  # noqa: E402
    build_query,
    format_row,
    format_summary,
    load_config,
    parse_natural_query,
    summarize_query,
)
from telegram_query_bot import format_card  # noqa: E402

app = Flask(__name__)
CONFIG = load_config()


_LEADING_NOISE = re.compile(r'^[查問幫我看]+')
_TRAILING_NOISE = re.compile(r'(實價|成交|登錄|資料|紀錄|記錄)+$')
_SECTION_LAND_NO = re.compile(r'^([一-鿿]{2,8})(\d[\d\-]+)$')


def _preprocess(text: str) -> str:
    t = _LEADING_NOISE.sub('', text.strip())
    t = _TRAILING_NOISE.sub('', t).strip()
    m = _SECTION_LAND_NO.match(t)
    if m:
        t = m.group(1) + '段' + m.group(2)
    return t or text.strip()


def make_reply(text: str) -> tuple[str, dict, str, list, int]:
    params, _ = parse_natural_query(_preprocess(text), CONFIG)
    if not any(params.get(k) for k in ('city', 'district', 'section_name', 'road', 'keyword')):
        reply = '請提供縣市、行政區、地段或關鍵地名，例如：大園區、大興路、內興段。'
        return reply, params, '', [], 0

    sql, values = build_query(params)
    with sqlite3.connect(DB_PATH) as conn:
        rows = [format_row(row) for row in conn.execute(sql, values).fetchall()]

    summary = summarize_query(params, db_path=DB_PATH)
    if not rows:
        reply = f'查無資料。\n\n{format_summary(summary)}'
        return reply, params, sql, values, summary['count']

    lines = ['土地戰情室查詢結果', format_summary(summary), '', '前5筆成交：']
    for row in rows[:5]:
        lines.append(format_card(row))
    reply = '\n'.join(lines)
    return reply, params, sql, values, summary['count']


def write_debug_log(text: str, params: dict, sql: str, values: list, hit_count: int, reply: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    safe_params = {k: v for k, v in params.items() if not k.startswith('_')}
    reply_lines = [line for line in reply.splitlines() if line.strip()]
    payload = {
        'time': datetime.now().isoformat(timespec='seconds'),
        'source': 'telegram_query_api',
        'input': text,
        'parsed': safe_params,
        'parser_steps': params.get('_debug', {}).get('steps', []),
        'sql': sql,
        'values': values,
        'hit_count': hit_count,
        'reply_summary': reply_lines[:8],
    }
    with LOG_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(payload, ensure_ascii=False) + '\n')


@app.post('/query')
def query():
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()
    if not text:
        return jsonify({'reply': '請輸入查詢文字。'}), 400

    try:
        reply, params, sql, values, hit_count = make_reply(text)
        write_debug_log(text, params, sql, values, hit_count, reply)
        return jsonify({'reply': reply})
    except Exception as exc:
        error_reply = f'查詢系統錯誤：{exc}'
        write_debug_log(text, {}, '', [], 0, error_reply)
        return jsonify({'reply': error_reply}), 500


@app.get('/health')
def health():
    return jsonify({'ok': True, 'service': 'telegram_query_api'})


if __name__ == '__main__':
    socket.getfqdn = lambda name='': name
    app.run(host='127.0.0.1', port=8000)
