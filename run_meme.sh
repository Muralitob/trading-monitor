#!/bin/bash
# Robinhood Chain MEME 扫描 · 每 15 min
cd /root/trading-monitor
source .env
git pull --rebase --autostash --quiet 2>/dev/null || true
/usr/bin/python3 meme_monitor.py
