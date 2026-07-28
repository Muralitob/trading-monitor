"""
交易价格监控脚本
每 5 分钟检查关键价位突破 + 大盘异动，触发时推送到 Telegram
"""
import os
import json
import urllib.request
import datetime

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT"]

# 关键位规则：dir=up 表示从下向上触及（阻力位）；dir=down 表示从上向下跌破（支撑位）
RULES = {
    "HYPEUSDT": [
        {"level": 58.0, "dir": "up",   "desc": "反弹到 58 阻力（做空关注区）"},
        {"level": 60.0, "dir": "up",   "desc": "反弹到 60 关键阻力"},
        {"level": 52.6, "dir": "down", "desc": "跌破 52.6 支撑（60日低点）"},
    ],
    "MUUSDT": [
        {"level": 885.0, "dir": "up",   "desc": "反弹到 885 阻力区（做空关注）"},
        {"level": 900.0, "dir": "up",   "desc": "反弹到 900 关键阻力"},
        {"level": 835.0, "dir": "down", "desc": "跌破 835 支撑"},
    ],
}

# 大盘异动监控：4H K线振幅超阈值时提醒
VOLATILITY_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
VOL_THRESHOLD = 0.03  # 3%


def klines(symbol, interval, limit):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    return json.loads(urllib.request.urlopen(url, timeout=10).read())


def send_tg(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main():
    now_utc = datetime.datetime.utcnow()
    bj_hour = (now_utc.hour + 8) % 24

    if 0 <= bj_hour < 7:
        print(f"Quiet hours (BJ {bj_hour:02d}:xx), skipping")
        return

    alerts = []

    for symbol, rules in RULES.items():
        try:
            # 拉最近 3 根 5m K线：[-3]上一根收盘, [-2]刚收盘, [-1]当前未收盘
            k = klines(symbol, "5m", 3)
            prev_prev_close = float(k[-3][4])
            prev_close = float(k[-2][4])
            current = float(k[-1][4])

            for r in rules:
                level = r["level"]
                if r["dir"] == "up":
                    # 从下向上穿越：上一根在下方，刚收盘的这一根到达或突破
                    if prev_prev_close < level and prev_close >= level:
                        alerts.append((symbol, current, r["desc"], "⬆️"))
                else:
                    if prev_prev_close > level and prev_close <= level:
                        alerts.append((symbol, current, r["desc"], "⬇️"))
        except Exception as e:
            print(f"[{symbol}] error: {e}")

    for symbol in VOLATILITY_SYMBOLS:
        try:
            k = klines(symbol, "4h", 2)
            last_closed = k[-2]
            open_p = float(last_closed[1])
            close_p = float(last_closed[4])
            close_ts = last_closed[6] / 1000  # closeTime 毫秒转秒
            # 只在 4H K线刚收盘的 6 分钟内触发一次（cron 5min 抖动余量）
            age = now_utc.timestamp() - close_ts
            if 0 <= age < 360:
                change = (close_p - open_p) / open_p
                if abs(change) >= VOL_THRESHOLD:
                    arrow = "📈" if change > 0 else "📉"
                    alerts.append((symbol, close_p, f"4H 振幅 {change*100:+.2f}%", arrow))
        except Exception as e:
            print(f"[{symbol}] error: {e}")

    if alerts:
        lines = ["🔔 *交易信号*", ""]
        for sym, price, desc, arrow in alerts:
            lines.append(f"{arrow} *{sym}*  `${price}`")
            lines.append(f"  {desc}")
            lines.append("")
        lines.append(f"_{now_utc.strftime('%m-%d %H:%M')} UTC_")
        send_tg("\n".join(lines))
        print(f"Sent {len(alerts)} alerts")
    else:
        print("No alerts triggered")


if __name__ == "__main__":
    main()
