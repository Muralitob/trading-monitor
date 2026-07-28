"""
交易价格监控脚本
1. 关键位穿越（HYPE / MU 手动配置的价位）
2. 大盘异动（BTC / ETH 4H 振幅≥3%）
3. 通道识别（SOXL 等：2H 平行通道，触碰上/下沿告警）
"""
import os
import json
import urllib.request
import datetime

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT"]

# ==================== 规则配置 ====================

# 关键位规则：dir=up 从下向上触及（阻力）；dir=down 从上向下跌破（支撑）
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

VOLATILITY_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
VOL_THRESHOLD = 0.03  # 4H 振幅阈值

CHANNEL_SYMBOLS = ["SOXLUSDT", "HYPEUSDT", "MUUSDT"]  # 跑通道识别


# ==================== 工具函数 ====================

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


# ==================== 通道识别 ====================

def find_swings(highs, lows, window=3):
    """返回 [(index, price)] for swing highs 和 swing lows"""
    swing_h, swing_l = [], []
    for i in range(window, len(highs) - window):
        if all(highs[i] >= highs[i + j] for j in range(-window, window + 1) if j != 0):
            swing_h.append((i, highs[i]))
        if all(lows[i] <= lows[i + j] for j in range(-window, window + 1) if j != 0):
            swing_l.append((i, lows[i]))
    return swing_h, swing_l


def fit_line(points):
    """最小二乘拟合 -> (slope, intercept, r_squared)"""
    n = len(points)
    if n < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return slope, intercept, r2


def _score_channel(upper_pts, lower_pts, n_bars, cur_price):
    """给一组 (upper_pts, lower_pts) 打分，返回 (score, channel_dict) 或 None"""
    if len(upper_pts) < 2 or len(lower_pts) < 2:
        return None
    u = fit_line(upper_pts)
    l = fit_line(lower_pts)
    if not u or not l:
        return None
    us, ui, ur2 = u
    ls, li, lr2 = l

    # 平行度：斜率差 < 30%
    if abs(us) < 1e-9 and abs(ls) < 1e-9:
        slope_diff = 0
    elif abs(us) < 1e-9 or abs(ls) < 1e-9:
        return None
    else:
        slope_diff = abs(us - ls) / max(abs(us), abs(ls))
    if slope_diff > 0.3:
        return None

    # 方向一致
    if us * ls < 0 and abs(us) > 1e-6 and abs(ls) > 1e-6:
        return None

    cur_idx = n_bars - 1
    upper_now = us * cur_idx + ui
    lower_now = ls * cur_idx + li
    if upper_now <= lower_now:
        return None
    width = upper_now - lower_now
    if width < cur_price * 0.02 or width > cur_price * 0.5:
        return None

    # 通道未失效：现价必须在通道范围附近（-20% ~ 120%）
    position = (cur_price - lower_now) / width
    if position < -0.2 or position > 1.2:
        return None

    # 上沿或下沿至少有一边拟合得好（有明确边界），另一边可以是震荡
    if max(ur2, lr2) < 0.85:
        return None
    if min(ur2, lr2) < 0.4:
        return None

    # 综合分：R²乘积 × 触点数 / (1+斜率差)
    score = ur2 * lr2 * (len(upper_pts) + len(lower_pts)) / (1 + slope_diff * 5)

    direction = "下降通道" if us < -1e-6 else ("上升通道" if us > 1e-6 else "水平通道")
    return score, {
        "direction": direction,
        "upper": upper_now,
        "lower": lower_now,
        "current": cur_price,
        "touch_upper": len(upper_pts),
        "touch_lower": len(lower_pts),
        "r2_upper": ur2,
        "r2_lower": lr2,
        "slope_upper": us,
        "slope_lower": ls,
    }


def detect_channel(k2h):
    """在 2H K线上识别平行通道；试多种 window 和 swing 数量组合，取最优"""
    if len(k2h) < 60:
        return None
    highs = [float(k[2]) for k in k2h]
    lows = [float(k[3]) for k in k2h]
    closes = [float(k[4]) for k in k2h]
    n = len(k2h)
    cur = closes[-1]

    best = None
    for window in (3, 5, 7):
        sh, sl = find_swings(highs, lows, window=window)
        # 试用最近 2/3/4/5 个 swing 组合
        for nu in (5, 4, 3, 2):
            for nl in (5, 4, 3, 2):
                if len(sh) < nu or len(sl) < nl:
                    continue
                result = _score_channel(sh[-nu:], sl[-nl:], n, cur)
                if result is None:
                    continue
                score, ch = result
                if best is None or score > best[0]:
                    best = (score, ch)

    return best[1] if best else None


def check_channel(symbol):
    """检查 symbol 是否触碰通道边界，返回 alert dict 或 None"""
    k2h = klines(symbol, "2h", 200)
    ch = detect_channel(k2h)
    if not ch:
        return None

    k5m = klines(symbol, "5m", 3)
    prev_prev_close = float(k5m[-3][4])
    prev_close = float(k5m[-2][4])
    current = float(k5m[-1][4])

    upper = ch["upper"]
    lower = ch["lower"]
    tol = (upper - lower) * 0.05  # 5% 通道宽度作为触碰容差

    # 触碰下沿：本次收盘触到但没跌穿，且上一根还没触到
    if lower - tol <= prev_close <= lower + tol and prev_prev_close > lower + tol:
        sig = "做多关注" if ch["direction"] == "上升通道" else "反弹关注（**逆势，谨慎**）"
        return {
            "symbol": symbol, "type": "触碰下沿", "channel": ch,
            "price": current, "signal": sig,
        }

    # 触碰上沿
    if upper - tol <= prev_close <= upper + tol and prev_prev_close < upper - tol:
        sig = "做空关注" if ch["direction"] == "下降通道" else "回落关注（**逆势，谨慎**）"
        return {
            "symbol": symbol, "type": "触碰上沿", "channel": ch,
            "price": current, "signal": sig,
        }

    return None


# ==================== 主流程 ====================

def main():
    now_utc = datetime.datetime.utcnow()
    bj_hour = (now_utc.hour + 8) % 24

    if 0 <= bj_hour < 7:
        print(f"Quiet hours (BJ {bj_hour:02d}:xx), skipping")
        return

    alerts = []

    # 1. 关键位穿越
    for symbol, rules in RULES.items():
        try:
            k = klines(symbol, "5m", 3)
            prev_prev_close = float(k[-3][4])
            prev_close = float(k[-2][4])
            current = float(k[-1][4])
            for r in rules:
                lv = r["level"]
                if r["dir"] == "up" and prev_prev_close < lv and prev_close >= lv:
                    alerts.append(("level", symbol, current, r["desc"], "⬆️"))
                elif r["dir"] == "down" and prev_prev_close > lv and prev_close <= lv:
                    alerts.append(("level", symbol, current, r["desc"], "⬇️"))
        except Exception as e:
            print(f"[level:{symbol}] error: {e}")

    # 2. 大盘异动
    for symbol in VOLATILITY_SYMBOLS:
        try:
            k = klines(symbol, "4h", 2)
            last_closed = k[-2]
            open_p = float(last_closed[1])
            close_p = float(last_closed[4])
            close_ts = last_closed[6] / 1000
            age = now_utc.timestamp() - close_ts
            if 0 <= age < 360:
                change = (close_p - open_p) / open_p
                if abs(change) >= VOL_THRESHOLD:
                    arrow = "📈" if change > 0 else "📉"
                    alerts.append(("vol", symbol, close_p, f"4H 振幅 {change*100:+.2f}%", arrow))
        except Exception as e:
            print(f"[vol:{symbol}] error: {e}")

    # 3. 通道识别
    channel_alerts = []
    for symbol in CHANNEL_SYMBOLS:
        try:
            ca = check_channel(symbol)
            if ca:
                channel_alerts.append(ca)
        except Exception as e:
            print(f"[channel:{symbol}] error: {e}")

    if not alerts and not channel_alerts:
        print("No alerts triggered")
        return

    # 组装消息
    lines = ["🔔 *交易信号*", ""]
    for kind, sym, price, desc, arrow in alerts:
        lines.append(f"{arrow} *{sym}*  `${price}`")
        lines.append(f"  {desc}")
        lines.append("")
    for ca in channel_alerts:
        ch = ca["channel"]
        icon = "🔻" if ca["type"] == "触碰下沿" else "🔺"
        lines.append(f"{icon} *{ca['symbol']}* 2H {ch['direction']} · {ca['type']}")
        lines.append(f"  现价 `${ca['price']}`")
        lines.append(f"  上沿 `${ch['upper']:.2f}` / 下沿 `${ch['lower']:.2f}`")
        lines.append(f"  触点 {ch['touch_upper']}/{ch['touch_lower']}  R² {ch['r2_upper']:.2f}/{ch['r2_lower']:.2f}")
        lines.append(f"  {ca['signal']}")
        lines.append("")
    lines.append(f"_{now_utc.strftime('%m-%d %H:%M')} UTC · 仅监控提醒，不自动下单_")
    send_tg("\n".join(lines))
    print(f"Sent: {len(alerts)} level/vol + {len(channel_alerts)} channel alerts")


if __name__ == "__main__":
    main()
