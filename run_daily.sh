#!/bin/bash
# 每日行情分析（每天 8:00 BJ = 00:00 UTC 触发）
cd /root/trading-monitor
source .env
git pull --rebase --autostash --quiet 2>/dev/null || true
/usr/bin/python3 daily_analysis.py
