#!/bin/bash
# WARP 看门狗（VPS cron 每 5 分钟跑一次）
# 1. 测币安 IPv4 连通性
# 2. 不通 → 记下断开时间，重启 warp-svc，注册丢了就重新注册，再 connect
# 3. 恢复后 → Telegram 推送"断了多久"
#
# crontab：*/5 * * * * /bin/bash /root/trading-monitor/warp_watchdog.sh >> /var/log/warp-watchdog.log 2>&1

cd /root/trading-monitor || exit 1
exec 9>/tmp/warp_watchdog.lock
flock -n 9 || exit 0

DOWN_FILE=/root/.warp_down_since
WARP="timeout 30 warp-cli --accept-tos"
now() { date '+%F %T'; }

is_up() {
    [ "$(curl -4 -s -m 10 -o /dev/null -w '%{http_code}' https://fapi.binance.com/fapi/v1/ping)" = "200" ]
}

send_tg() {
    set -a; . ./.env; set +a
    curl -4 -s -m 15 -o /dev/null "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=$1"
}

if is_up; then
    if [ -f "$DOWN_FILE" ]; then
        since=$(cat "$DOWN_FILE")
        mins=$(( ($(date +%s) - since) / 60 ))
        down_at=$(TZ=Asia/Shanghai date -d "@$since" '+%m-%d %H:%M')
        if send_tg "🔧 监控网络已恢复：WARP 从北京时间 ${down_at} 起断了约 ${mins} 分钟，期间的信号都没推送，已自动修复"; then
            rm -f "$DOWN_FILE"
            echo "$(now) recovered after ${mins} min, notified"
        else
            echo "$(now) up but telegram failed, will retry"
        fi
    fi
    exit 0
fi

[ -f "$DOWN_FILE" ] || date +%s > "$DOWN_FILE"
echo "$(now) binance unreachable, repairing warp"

$WARP connect >/dev/null 2>&1
sleep 10
is_up && { echo "$(now) fixed by connect"; exit 0; }

timeout 120 systemctl restart warp-svc
sleep 15
if $WARP status 2>&1 | grep -qi "registration missing"; then
    echo "$(now) registration missing, registering new"
    $WARP registration new
fi
$WARP mode warp
$WARP connect
sleep 15

if is_up; then
    echo "$(now) fixed by restart/register"
else
    echo "$(now) still down: $($WARP status 2>&1 | tr '\n' ' ')"
fi
