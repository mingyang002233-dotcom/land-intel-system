#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram 查詢機器人
使用 getUpdates polling，支援分頁查詢與本機 session 暫存。
"""

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))
from query_land import (load_config, parse_natural_query, build_query, format_row,
                        build_display_metrics, build_ranking, format_ranking,
                        summarize_results, format_summary, suggest_similar,
                        log_query_debug, summarize_query)

DB_PATH = PROJECT_ROOT / 'db' / 'land_intel.db'
MAX_MESSAGE_LENGTH = 3500
PAGE_SIZE = 5


def _fmt_total(total_wan):
    if total_wan is None:
        return 'N/A'
    if total_wan >= 10000:
        return f'{total_wan / 10000:.2f}億'
    return f'{total_wan:.0f}萬'


def format_card(row):
    place = f'{row["city"]}{row["district"] or ""}' if row.get("district") else (row.get("city") or '')
    section = row.get('section_name') or ''
    land_num = row.get('land_number') or ''
    loc_raw = row.get('location_raw') or ''
    trade_date = row.get('trade_date') or ''
    land_use_zone = row.get('land_use_zone') or ''
    note = row.get('note') or ''
    txn = row.get('transaction_target') or ''
    total = row.get('total_price_wan')

    # 從 location_raw 補抓地號
    if not land_num and '地號' in loc_raw:
        m = re.search(r'(\d[\d\-\/]*)\s*地號', loc_raw)
        if m:
            land_num = m.group(1)

    section_part = section if section else ''
    land_part = f'{land_num}地號' if land_num else ''

    if section_part or land_part:
        line1 = f'📍 {place}｜{section_part}｜{land_part}'
    else:
        is_address = '地號' not in loc_raw and any(k in loc_raw for k in ('路', '街', '巷', '弄', '號'))
        line1 = f'📍 {place}｜{loc_raw}' if is_address else f'📍 {place}'

    metrics = build_display_metrics(row)
    area_val = f'{metrics["area_ping"]:.1f}坪' if metrics.get('area_ping') else 'N/A'
    unit_val = f'{metrics["unit_price"]:.1f}萬/坪' if metrics.get('unit_price') else 'N/A'
    total_val = _fmt_total(total)
    zone_str = f'（{land_use_zone}）' if land_use_zone else ''
    txn_str = f'{txn}' if txn else ''

    lines = [
        line1,
        f'📐 {area_val}｜💰 {total_val}｜💵 {unit_val}',
        f'📅 成交：{trade_date}　{txn_str}{zone_str}',
    ]
    if note:
        lines.append(f'📝 備註：{note}')
    if metrics.get('warning'):
        lines.append(metrics['warning'])
    lines.append('━━━━━━━━━━')
    return '\n'.join(lines)


def load_dotenv(paths=None):
    if paths is None:
        paths = [PROJECT_ROOT / '.env', PROJECT_ROOT.parent / '.env']
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


class TelegramQueryBot:
    def __init__(self, token=None, db_path=None, poll_timeout=30):
        load_dotenv()
        self.token = token or os.environ.get('TELEGRAM_BOT_TOKEN')
        if not self.token:
            raise RuntimeError('TELEGRAM_BOT_TOKEN is required')
        self.db_path = db_path or str(DB_PATH)
        self.config = load_config()
        self.poll_timeout = poll_timeout
        self.api_url = f'https://api.telegram.org/bot{self.token}'
        self.offset = None
        self.sessions = {}

    def get_updates(self):
        params = {'timeout': self.poll_timeout, 'allowed_updates': json.dumps(['message'])}
        if self.offset is not None:
            params['offset'] = self.offset
        url = f"{self.api_url}/getUpdates?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=self.poll_timeout + 10) as resp:
            data = resp.read().decode('utf-8')
            return json.loads(data)

    def send_message(self, chat_id, text):
        for chunk in self.split_message(text):
            payload = {
                'chat_id': chat_id,
                'text': chunk,
                'disable_web_page_preview': True
            }
            data = urllib.parse.urlencode(payload).encode('utf-8')
            req = urllib.request.Request(f'{self.api_url}/sendMessage', data=data)
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            with urllib.request.urlopen(req, timeout=15) as resp:
                json.loads(resp.read().decode('utf-8'))

    def split_message(self, text):
        if len(text) <= MAX_MESSAGE_LENGTH:
            return [text]
        lines = text.split('\n')
        chunks = []
        current = ''
        for line in lines:
            candidate = f'{current}\n{line}' if current else line
            if len(candidate) > MAX_MESSAGE_LENGTH:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def process_update(self, update):
        if 'message' not in update:
            return
        message = update['message']
        if 'text' not in message:
            return

        chat_id = message['chat']['id']
        text = message['text'].strip()
        if not text:
            return

        if text.startswith('/start'):
            reply = self.start_message()
        elif text == '下一頁':
            reply = self.next_page(chat_id)
        elif text == '上一頁':
            reply = self.previous_page(chat_id)
        else:
            reply = self.handle_query(chat_id, text)

        try:
            self.send_message(chat_id, reply)
        except Exception as e:
            print('Failed to send message:', e)

    def handle_query(self, chat_id, text):
        # 排行模式：訊息包含「排行」關鍵字
        is_rank = '排行' in text
        clean_text = text.replace('排行', '').strip()

        params, query_text = parse_natural_query(clean_text or text, self.config)
        if 'city' not in params and 'district' not in params and 'section_name' not in params and 'road' not in params and 'keyword' not in params:
            return '請提供縣市、行政區、地段或關鍵地名，例如：桃園市、新林段、三塊石。'

        if is_rank:
            rows = build_ranking(params, db_path=self.db_path)
            return format_ranking(rows, params)

        sql, values = build_query(params)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(sql, values)
            rows = [format_row(row) for row in cursor.fetchall()]
        summary = summarize_query(params, db_path=self.db_path)
        log_query_debug(text, params, sql, values, summary['count'], db_path=self.db_path)

        if not rows:
            suggestions = suggest_similar(params, db_path=self.db_path)
            return self.no_result_response(params, suggestions)

        session = {
            'query_text': query_text,
            'params': params,
            'rows': rows,
            'summary': summary,
            'page': 1
        }
        self.sessions[chat_id] = session
        return self.build_page_response(chat_id)

    def no_result_response(self, params, suggestions):
        parts = []
        if params.get('section_name'):
            parts.append(f'地段：{params["section_name"]}')
        if params.get('road'):
            parts.append(f'路名：{params["road"]}')
        if params.get('district'):
            parts.append(f'行政區：{params["district"]}')
        if params.get('start_date'):
            parts.append(f'時間：{params["start_date"]} 之後')
        label = '、'.join(parts) if parts else '目前條件'
        lines = [f'查無資料：{label}']
        if suggestions:
            lines += ['', '你是不是想查：']
            lines += [f'・{s}' for s in suggestions[:5]]
        else:
            lines += ['', '可嘗試放寬時間，例如「近一年」，或改用行政區／路名／地段查詢。']
        return '\n'.join(lines)

    def next_page(self, chat_id):
        session = self.sessions.get(chat_id)
        if not session:
            return '尚無查詢紀錄，請先輸入查詢條件。'
        max_page = max(1, (len(session['rows']) + PAGE_SIZE - 1) // PAGE_SIZE)
        if session['page'] >= max_page:
            return f'已顯示最後一頁 ({max_page})。'
        session['page'] += 1
        return self.build_page_response(chat_id)

    def previous_page(self, chat_id):
        session = self.sessions.get(chat_id)
        if not session:
            return '尚無查詢紀錄，請先輸入查詢條件。'
        if session['page'] <= 1:
            return '已顯示第一頁。'
        session['page'] -= 1
        return self.build_page_response(chat_id)

    def build_page_response(self, chat_id):
        session = self.sessions.get(chat_id)
        if not session:
            return '尚無查詢紀錄。'

        rows = session['rows']
        page = session['page']
        total = len(rows)
        max_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        page_rows = rows[start:end]

        if not page_rows:
            return '此頁無資料。'

        lines = [f'第 {page}/{max_page} 頁', format_summary(session.get('summary') or summarize_results(rows)), '']
        for row in page_rows:
            lines.append(format_card(row))

        if page < max_page:
            lines.append(f'輸入「下一頁」查看第 {page + 1} 頁')
        if page > 1:
            lines.append('輸入「上一頁」返回上一頁')

        return '\n'.join(lines)

    def start_message(self):
        return (
            '歡迎使用 LAND 查詢 Bot！\n'
            '請輸入自然語查詢，例如：\n'
            '• 泰山建地 近四個月成交\n'
            '• 泰山 建地 近四個月成交\n'
            '• 大園農地500坪以上\n'
            '支援「下一頁」「上一頁」查詢。'
        )

    def run(self):
        print('Telegram bot started. Polling for updates...')
        while True:
            try:
                result = self.get_updates()
                if not result.get('ok'):
                    print('getUpdates response not ok:', result)
                    time.sleep(5)
                    continue
                updates = result.get('result', [])
                for update in updates:
                    self.offset = update['update_id'] + 1
                    try:
                        self.process_update(update)
                    except Exception as e:
                        print('Error processing update:', e)
                time.sleep(1)
            except KeyboardInterrupt:
                print('Bot stopped by user.')
                break
            except urllib.error.HTTPError as e:
                print('HTTPError:', e)
                time.sleep(5)
            except urllib.error.URLError as e:
                print('URLError:', e)
                time.sleep(5)
            except Exception as e:
                print('Unexpected error:', e)
                time.sleep(5)


if __name__ == '__main__':
    bot = TelegramQueryBot()
    bot.run()
