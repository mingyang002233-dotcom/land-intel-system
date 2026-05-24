#!/usr/bin/env python3
"""
download_watcher.py
監控 ~/Downloads，偵測 foundi YAML 檔案，自動搬移到 電傳解析/ 資料夾。

支援副檔名：
  *.yaml  *.yml  *.yaml.txt  *.yaml.txt.yml

搬移規則：
  - 目的地已有同名檔案 → 加 timestamp 後綴，不覆蓋
  - 所有動作寫入 logs/download_watcher.log

啟動：
  python3 scripts/download_watcher.py            # 前景執行
  python3 scripts/download_watcher.py --once     # 只掃一次，不常駐
"""

import argparse
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ── 路徑設定 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WATCH_DIR    = Path.home() / 'Downloads'
DEST_DIR     = PROJECT_ROOT / '電傳解析'
LOG_FILE     = PROJECT_ROOT / 'logs' / 'download_watcher.log'

YAML_SUFFIXES = {'.yaml', '.yml'}
YAML_PATTERNS = ('.yaml', '.yml', '.yaml.txt', '.yaml.txt.yml')


# ── Logger ────────────────────────────────────────────────────────────────────
def _setup_logger() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('download_watcher')
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = _setup_logger()


# ── 檔案判斷 ─────────────────────────────────────────────────────────────────
def _is_yaml_file(p: Path) -> bool:
    name = p.name
    return any(name.endswith(pat) for pat in YAML_PATTERNS)


def _dest_path(src: Path) -> Path:
    """回傳不衝突的目的地路徑。若同名已存在，加 timestamp。"""
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    candidate = DEST_DIR / src.name
    if not candidate.exists():
        return candidate
    # 加 timestamp：原檔名（不含副檔名）+ _HHMMSSff + 副檔名
    ts = datetime.now().strftime('%H%M%S%f')[:8]
    # 找最長符合的後綴
    stem = src.name
    suffix = ''
    for pat in sorted(YAML_PATTERNS, key=len, reverse=True):
        if stem.endswith(pat):
            stem = stem[: -len(pat)]
            suffix = pat
            break
    return DEST_DIR / f'{stem}_{ts}{suffix}'


# ── 搬移動作 ─────────────────────────────────────────────────────────────────
def move_file(src: Path):
    if not src.exists():
        return
    if not src.is_file():
        return
    if not _is_yaml_file(src):
        return

    dest = _dest_path(src)
    renamed = dest.name != src.name

    try:
        shutil.move(str(src), str(dest))
        if renamed:
            log.info(f'MOVE(rename)  {src}  →  {dest}')
        else:
            log.info(f'MOVE          {src}  →  {dest}')
    except Exception as e:
        log.error(f'MOVE FAILED   {src}  →  {dest}  ({e})')


# ── Watchdog handler ──────────────────────────────────────────────────────────
class YamlMoveHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _is_yaml_file(p):
            # 短暫等待確保檔案寫入完成（瀏覽器下載有短暫鎖定期）
            time.sleep(0.5)
            log.debug(f'DETECTED      {p}')
            move_file(p)

    def on_moved(self, event):
        # 瀏覽器有時先寫 .crdownload 再 rename 成正式名稱
        if event.is_directory:
            return
        p = Path(event.dest_path)
        if _is_yaml_file(p):
            time.sleep(0.3)
            log.debug(f'RENAMED→      {p}')
            move_file(p)


# ── 一次性掃描（啟動時補掃既有檔案）────────────────────────────────────────
def scan_existing():
    found = [p for p in WATCH_DIR.iterdir() if p.is_file() and _is_yaml_file(p)]
    if found:
        log.info(f'STARTUP SCAN  {len(found)} 個既有 YAML 檔案')
        for p in found:
            move_file(p)
    else:
        log.info('STARTUP SCAN  無既有 YAML 檔案')


# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='Downloads → 電傳解析 自動搬檔')
    ap.add_argument('--once', action='store_true',
                    help='只掃一次既有檔案後結束，不常駐監控')
    args = ap.parse_args()

    log.info(f'=== download_watcher 啟動 ===')
    log.info(f'監控：{WATCH_DIR}')
    log.info(f'目的：{DEST_DIR}')
    log.info(f'日誌：{LOG_FILE}')

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    scan_existing()

    if args.once:
        log.info('--once 模式，掃描完畢後結束')
        return

    handler  = YamlMoveHandler()
    observer = Observer()
    observer.schedule(handler, str(WATCH_DIR), recursive=False)
    observer.start()
    log.info('監控中… (Ctrl+C 停止)')

    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        log.info('收到中斷信號，停止監控')
        observer.stop()

    observer.join()
    log.info('=== download_watcher 結束 ===')


if __name__ == '__main__':
    main()
