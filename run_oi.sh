#!/bin/bash
# 追龙扫描器 · 每 5 分钟触发（原 OI 异动扫描已停用，oi_monitor.py 保留备查）
cd /root/trading-monitor
source .env
git pull --rebase --autostash --quiet 2>/dev/null || true
/usr/bin/python3 dragon.py
