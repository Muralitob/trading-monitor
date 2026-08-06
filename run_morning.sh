#!/bin/bash
# 早间行情快报 · 每天 07:30 BJ = 23:30 UTC 前一天
cd /root/trading-monitor
source .env
git pull --rebase --autostash --quiet 2>/dev/null || true
/usr/bin/python3 morning_brief.py
