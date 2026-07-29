#!/bin/bash
# VPS 一键部署脚本
# 用法：curl -fsSL https://raw.githubusercontent.com/Muralitob/trading-monitor/main/setup.sh | TG_TOKEN=xxx TG_CHAT=xxx bash

set -e

echo "==> 检查环境变量"
if [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT" ]; then
    echo "错误：请设置 TG_TOKEN 和 TG_CHAT 环境变量"
    exit 1
fi

echo "==> 安装依赖"
apt-get update -qq
apt-get install -y -qq git python3 python3-matplotlib curl cron

REPO_DIR=/root/trading-monitor

echo "==> 拉代码"
if [ -d "$REPO_DIR" ]; then
    cd "$REPO_DIR"
    git pull --rebase --autostash
else
    git clone https://github.com/Muralitob/trading-monitor.git "$REPO_DIR"
    cd "$REPO_DIR"
fi

echo "==> 写入 .env"
cat > "$REPO_DIR/.env" <<EOF
export TG_TOKEN="$TG_TOKEN"
export TG_CHAT="$TG_CHAT"
export WECOM_KEY="${WECOM_KEY:-}"
export PUSHPLUS_TOKEN="${PUSHPLUS_TOKEN:-}"
EOF
chmod 600 "$REPO_DIR/.env"

echo "==> 生成 run_once.sh"
cat > "$REPO_DIR/run_once.sh" <<'RUNEOF'
#!/bin/bash
cd /root/trading-monitor
source .env
# 静默 git pull（拿最新代码）
git pull --rebase --autostash --quiet 2>/dev/null || true
# 跑监控
/usr/bin/python3 monitor.py
RUNEOF
chmod +x "$REPO_DIR/run_once.sh"

echo "==> 配置 crontab（每分钟执行）"
CRON_LINE="* * * * * /root/trading-monitor/run_once.sh >> /var/log/trading-monitor.log 2>&1"
# 移除旧的，添加新的
(crontab -l 2>/dev/null | grep -v "trading-monitor/run_once.sh"; echo "$CRON_LINE") | crontab -

echo "==> 首次运行测试"
source "$REPO_DIR/.env"
cd "$REPO_DIR"
/usr/bin/python3 monitor.py && echo "✓ monitor.py 运行正常" || echo "✗ monitor.py 报错，见上"

echo ""
echo "======================================"
echo "✅ 部署完成"
echo "======================================"
echo "  监控路径:  $REPO_DIR"
echo "  Crontab:   每分钟执行"
echo "  日志:      tail -f /var/log/trading-monitor.log"
echo "  手动测试:  bash $REPO_DIR/run_once.sh"
echo "======================================"
