#!/bin/bash
# OI 异动扫描器 · 每 5 分钟触发
cd /root/trading-monitor
source .env
git pull --rebase --autostash --quiet 2>/dev/null || true
/usr/bin/python3 oi_monitor.py
