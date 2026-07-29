#!/bin/bash
# Mac launchd 每分钟触发此脚本
# 1. git pull（拿到 GH Actions 那边可能更新过的 state.json）
# 2. 跑 monitor.py
# 3. 如果 state 变了，commit + push

set -e
cd /Users/mura/Documents/invest/trading-monitor

# 环境变量（Token / Chat ID）
export TG_TOKEN="8618129888:AAHnK9ZEp159a81TThUogYTwvsq9D0F8N6E"
export TG_CHAT="1626067349"

# 用系统 python3（不要 conda / pyenv）
PYTHON=/usr/bin/python3

# 静默同步 state（不影响主流程）
git pull --rebase --autostash --quiet 2>/dev/null || true

# 记录当前 state.json 哈希
BEFORE_HASH=""
[ -f state.json ] && BEFORE_HASH=$(shasum state.json | cut -d' ' -f1)

# 跑监控
$PYTHON monitor.py

# 检查 state.json 是否变化
AFTER_HASH=""
[ -f state.json ] && AFTER_HASH=$(shasum state.json | cut -d' ' -f1)

# 如果变了才推送
if [ "$BEFORE_HASH" != "$AFTER_HASH" ]; then
    git add state.json
    git -c user.email="mac@monitor.local" -c user.name="mura-mac" \
        commit -m "state update from mac [skip ci]" --quiet 2>/dev/null || true
    git push --quiet 2>/dev/null || true
fi
