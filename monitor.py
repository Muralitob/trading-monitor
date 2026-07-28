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

# 所有关注的品种
ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT",              # 加密大盘
    "HYPEUSDT",                        # 加密山寨
    "XAUUSDT",                         # 黄金
    "SOXLUSDT", "KORUUSDT",            # 3x ETF
    "EWYUSDT",                         # 韩国 ETF
    "SKHYNIXUSDT", "SNDKUSDT",         # 半导体
    "MUUSDT",                          # 美光
]

VOLATILITY_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSDT"]
VOL_THRESHOLD = 0.03

CHANNEL_SYMBOLS = ALL_SYMBOLS  # 通道识别应用到全部
EMA_SYMBOLS = ALL_SYMBOLS       # EMA 检测应用到全部


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


# ==================== 反抽检测（Break & Retest） ====================

def check_retest(symbol, rules, lookback_hours=24):
    """
    检测破位后反抽到关键位：
      - 24 小时内 1H 收盘破位
      - 现在 5m 收盘从破位反方向回到关键位附近
    """
    try:
        k1h = klines(symbol, "1h", lookback_hours + 2)
        k5m = klines(symbol, "5m", 3)
    except Exception as e:
        print(f"[retest:{symbol}] fetch error: {e}")
        return []
    if len(k1h) < 3 or len(k5m) < 3:
        return []

    prev_prev_5m = float(k5m[-3][4])
    prev_5m = float(k5m[-2][4])
    current = float(k5m[-1][4])

    alerts = []
    for r in rules:
        level = r["level"]
        tol = level * 0.005  # 0.5% 容差判定"接近"

        # 查找 24h 内 1H K线是否有破位（收盘穿越）
        broke_down = False  # 从上方破到下方
        broke_up = False    # 从下方破到上方
        break_time = None

        for i in range(len(k1h) - 1):  # 不含最后一根（当前未收盘）
            o = float(k1h[i][1])
            c = float(k1h[i][4])
            if o > level and c < level - tol:
                broke_down = True
                break_time = k1h[i][6] / 1000
            elif o < level and c > level + tol:
                broke_up = True
                break_time = k1h[i][6] / 1000

        # 破位后反抽做空（做空关注）
        if broke_down and prev_prev_5m < level - tol and \
           (level - tol) <= prev_5m <= (level + tol):
            alerts.append({
                "symbol": symbol,
                "level": level,
                "type": "反抽阻力",
                "text": f"跌破 ${level:g} 后反抽 → **做空关注**",
                "price": current,
                "icon": "🔻",
            })

        # 突破后回踩做多（做多关注）
        if broke_up and prev_prev_5m > level + tol and \
           (level - tol) <= prev_5m <= (level + tol):
            alerts.append({
                "symbol": symbol,
                "level": level,
                "type": "回踩支撑",
                "text": f"突破 ${level:g} 后回踩 → **做多关注**",
                "price": current,
                "icon": "🔺",
            })

    return alerts


# ==================== EMA 检测 ====================

def ema_series(values, period):
    """返回完整 EMA 序列（长度和输入一致）"""
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _bar_just_closed(kline, now_ts, tolerance_sec=360):
    """判断 K线是否刚收盘（用于 4H / 1D 触发窗口）"""
    close_ts = kline[6] / 1000
    age = now_ts - close_ts
    return 0 <= age < tolerance_sec


def check_ema_signals(symbol, now_utc):
    """
    检查 EMA 相关信号：
      A. 4H 收盘触碰 EMA25 (0.5% 容差)
      B. 4H 收盘触碰 EMA100 (0.8% 容差)
      C. 日线收盘穿越 EMA50
    返回 alert list（每项是 dict）
    """
    now_ts = now_utc.timestamp()
    alerts = []

    # ==== 4H EMA25 / EMA100 触碰 ====
    try:
        k4h = klines(symbol, "4h", 150)
        if len(k4h) >= 105:
            closes = [float(k[4]) for k in k4h]
            ema25 = ema_series(closes, 25)
            ema100 = ema_series(closes, 100)

            # 刚收盘的 4H K 线（倒数第 2 根）
            last_closed = k4h[-2]
            if _bar_just_closed(last_closed, now_ts):
                close_p = closes[-2]
                prev_close = closes[-3]  # 上一根 4H 收盘

                # A. EMA25 触碰
                e25 = ema25[-2]
                e25_prev = ema25[-3]
                tol25 = e25 * 0.005
                # 触碰 = 本次 K线收盘距 EMA25 ≤ 0.5%，上一根 > 0.5%
                if abs(close_p - e25) <= tol25 and abs(prev_close - e25_prev) > tol25:
                    side = "上方" if close_p > e25 else "下方"
                    alerts.append({
                        "kind": "ema_touch",
                        "symbol": symbol,
                        "text": f"4H 收盘 {side}触碰 EMA25",
                        "detail": f"现价 ${close_p:.4f}  EMA25 ${e25:.4f}",
                        "icon": "📊",
                    })

                # B. EMA100 触碰
                e100 = ema100[-2]
                e100_prev = ema100[-3]
                tol100 = e100 * 0.008
                if abs(close_p - e100) <= tol100 and abs(prev_close - e100_prev) > tol100:
                    side = "上方" if close_p > e100 else "下方"
                    alerts.append({
                        "kind": "ema_touch",
                        "symbol": symbol,
                        "text": f"🌟 4H 收盘 {side}触碰 EMA100（大级别支撑）",
                        "detail": f"现价 ${close_p:.4f}  EMA100 ${e100:.4f}",
                        "icon": "📊",
                    })
    except Exception as e:
        print(f"[ema_4h:{symbol}] error: {e}")

    # ==== 日线 EMA50 穿越 ====
    try:
        k1d = klines(symbol, "1d", 100)
        if len(k1d) >= 55:
            closes_d = [float(k[4]) for k in k1d]
            ema50_d = ema_series(closes_d, 50)

            last_closed_d = k1d[-2]
            if _bar_just_closed(last_closed_d, now_ts):
                close_d = closes_d[-2]
                prev_close_d = closes_d[-3]
                e50 = ema50_d[-2]
                e50_prev = ema50_d[-3]

                if prev_close_d < e50_prev and close_d >= e50:
                    alerts.append({
                        "kind": "ema_cross",
                        "symbol": symbol,
                        "text": "🌟🌟 日线收盘上穿 EMA50（大趋势转多）",
                        "detail": f"收盘 ${close_d:.4f}  EMA50 ${e50:.4f}",
                        "icon": "🚀",
                    })
                elif prev_close_d > e50_prev and close_d <= e50:
                    alerts.append({
                        "kind": "ema_cross",
                        "symbol": symbol,
                        "text": "🌟🌟 日线收盘下穿 EMA50（大趋势转空）",
                        "detail": f"收盘 ${close_d:.4f}  EMA50 ${e50:.4f}",
                        "icon": "⚠️",
                    })
    except Exception as e:
        print(f"[ema_1d:{symbol}] error: {e}")

    return alerts


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

    # 4. EMA 触碰 / 穿越
    ema_alerts = []
    for symbol in EMA_SYMBOLS:
        ema_alerts.extend(check_ema_signals(symbol, now_utc))

    # 5. 破位反抽（对所有配了关键位的品种）
    retest_alerts = []
    for symbol, rules in RULES.items():
        retest_alerts.extend(check_retest(symbol, rules))

    if not alerts and not channel_alerts and not ema_alerts and not retest_alerts:
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
    for ea in ema_alerts:
        lines.append(f"{ea['icon']} *{ea['symbol']}*  {ea['text']}")
        lines.append(f"  {ea['detail']}")
        lines.append("")
    for ra in retest_alerts:
        lines.append(f"{ra['icon']} *{ra['symbol']}*  {ra['text']}")
        lines.append(f"  现价 `${ra['price']}`  关键位 `${ra['level']:g}`")
        lines.append("")
    lines.append(f"_{now_utc.strftime('%m-%d %H:%M')} UTC · 仅监控提醒，不自动下单_")
    send_tg("\n".join(lines))
    print(f"Sent: {len(alerts)} level/vol + {len(channel_alerts)} channel + {len(ema_alerts)} ema + {len(retest_alerts)} retest alerts")


if __name__ == "__main__":
    main()
