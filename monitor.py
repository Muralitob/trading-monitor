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
from pathlib import Path

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT"]
# 可选：企业微信群机器人 key（有则并行推送）
WECOM_KEY = os.environ.get("WECOM_KEY", "").strip()
# 可选：PushPlus token（微信推送）
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()
# 可选：Bark key（iOS 推送）
BARK_KEY = os.environ.get("BARK_KEY", "").strip()

# 状态文件（用于去重，避免 30 分钟窗口内重复告警）
STATE_FILE = Path(__file__).parent / "state.json"

def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_state(state):
    # 清理 48 小时以前的旧记录
    cutoff = datetime.datetime.utcnow().timestamp() - 48*3600
    state = {k: v for k, v in state.items() if v > cutoff}
    STATE_FILE.write_text(json.dumps(state, indent=2))

def already_fired(state, key):
    """检查此告警是否 24h 内已推过"""
    if key not in state:
        return False
    return (datetime.datetime.utcnow().timestamp() - state[key]) < 24*3600

def mark_fired(state, key):
    state[key] = datetime.datetime.utcnow().timestamp()

def today_key():
    d = datetime.datetime.utcnow()
    return d.strftime("%Y%m%d")

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


def _strip_md(text):
    """把 Telegram Markdown 转成 WeCom 能用的纯文本（去掉 `*` `_` `` ` ``）"""
    import re
    t = text
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)     # **粗体**
    t = re.sub(r"\*(.+?)\*", r"\1", t)         # *斜体*
    t = re.sub(r"_(.+?)_", r"\1", t)           # _斜体_
    t = t.replace("`", "")                     # 去除反引号
    return t


def send_wecom_text(text):
    """发文本到企业微信群机器人（WECOM_KEY 未设置则跳过）"""
    if not WECOM_KEY:
        return
    try:
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}"
        data = json.dumps({
            "msgtype": "text",
            "text": {"content": _strip_md(text)},
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[wecom text] {e}")


def send_wecom_image(photo_path):
    """发图片到企业微信群机器人"""
    if not WECOM_KEY:
        return
    try:
        import base64, hashlib
        with open(photo_path, "rb") as f:
            img = f.read()
        payload = {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(img).decode(),
                "md5": hashlib.md5(img).hexdigest(),
            }
        }
        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={WECOM_KEY}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[wecom image] {e}")


def send_bark(title, body):
    """Bark iOS 推送"""
    if not BARK_KEY:
        return
    try:
        payload = {
            "title": title,
            "body": _strip_md(body)[:2000],  # Bark 有长度限制
            "sound": "bell",
            "group": "trading-monitor",  # iOS 通知分组
        }
        req = urllib.request.Request(
            f"https://api.day.app/{BARK_KEY}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[bark] {e}")


def send_pushplus(title, content):
    """PushPlus 微信推送。content 用 markdown 格式。"""
    if not PUSHPLUS_TOKEN:
        return
    try:
        payload = {
            "token": PUSHPLUS_TOKEN,
            "title": title,
            "content": content,
            "template": "markdown",
        }
        req = urllib.request.Request(
            "https://www.pushplus.plus/send",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[pushplus] {e}")


def _extract_title(caption):
    """从 caption 提取标题（第一行）"""
    first_line = caption.split("\n", 1)[0]
    # 去 markdown 字符
    return _strip_md(first_line).strip()[:60] or "交易信号"


def push_all(caption, photo_path=None):
    """同时推 Telegram / 企业微信 / PushPlus（有配置才推）"""
    if photo_path:
        try: send_tg_photo(caption, photo_path)
        except Exception as e: print(f"[tg photo] {e}")
        send_wecom_image(photo_path)   # 企业微信：图 + 文分开
        send_wecom_text(caption)
    else:
        try: send_tg(caption)
        except Exception as e: print(f"[tg text] {e}")
        send_wecom_text(caption)

    title = _extract_title(caption)

    # PushPlus 只发文字
    if PUSHPLUS_TOKEN:
        content = caption
        if photo_path:
            content = content + "\n\n> 📊 详细图表见 Telegram"
        send_pushplus(title, content)

    # Bark iOS 推送
    if BARK_KEY:
        send_bark(title, caption)


def send_tg_photo(caption, photo_path):
    """发送图片 + 文字说明"""
    import uuid
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    boundary = uuid.uuid4().hex
    with open(photo_path, "rb") as f:
        photo_bytes = f.read()

    def part(name, value, filename=None, content_type=None):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename:
            head += f'; filename="{filename}"'
        head += "\r\n"
        if content_type:
            head += f"Content-Type: {content_type}\r\n"
        head += "\r\n"
        if isinstance(value, str):
            value = value.encode()
        return head.encode() + value + b"\r\n"

    body = b""
    body += part("chat_id", str(CHAT_ID))
    body += part("caption", caption)
    body += part("parse_mode", "Markdown")
    body += part("photo", photo_bytes, filename="chart.png", content_type="image/png")
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    urllib.request.urlopen(req, timeout=20).read()


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
        "upper_pts": list(upper_pts),
        "lower_pts": list(lower_pts),
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


def _anchor_str(k2h, idx, price):
    """把 K线 index + 价格 转成锚点字符串: $XXX @ MM-DD HH:MM"""
    try:
        ts = int(k2h[idx][0]) / 1000
        dt = datetime.datetime.utcfromtimestamp(ts)
        return f"${price:.4g} @ {dt.strftime('%m-%d %H:%M')}"
    except Exception:
        return f"${price:.4g}"


def check_channel(symbol, state, dk):
    """检查 symbol 是否触碰通道边界，返回 alert dict 或 None（带状态去重）"""
    k2h = klines(symbol, "2h", 200)
    ch = detect_channel(k2h)
    if not ch:
        return None

    k5m = klines(symbol, "5m", 8)  # 滑动窗口
    current = float(k5m[-1][4])

    upper = ch["upper"]
    lower = ch["lower"]
    tol = (upper - lower) * 0.05

    def _build_trade_meta(touched, ch, k2h, current):
        """构造交易参数：方向/止损/止盈/支撑压力/锚点"""
        highs_d = [float(x[2]) for x in k2h]
        lows_d = [float(x[3]) for x in k2h]
        upper = ch["upper"]; lower = ch["lower"]

        if touched == "upper":
            direction = "SHORT"
            entry = current
            # 止损：通道上沿上方，取最近 20 根内的最高点 * 1.003 或 上沿 * 1.008 取更远者
            recent_high = max(highs_d[-20:])
            stop = max(recent_high * 1.003, upper * 1.008)
            target = lower  # 通道另一侧
        else:
            direction = "LONG"
            entry = current
            recent_low = min(lows_d[-20:])
            stop = min(recent_low * 0.997, lower * 0.992)
            target = upper

        # 盈亏比
        risk = abs(entry - stop)
        reward = abs(target - entry)
        rr = reward / risk if risk > 0 else 0

        # 支撑/压力：最近的对手 swing 极值
        support = min(lows_d[-30:])
        resistance = max(highs_d[-30:])

        # 锚点（2H 通道对应的 swing 点）
        anchors_up = [_anchor_str(k2h, i, p) for i, p in ch.get("upper_pts", [])[-2:]]
        anchors_lo = [_anchor_str(k2h, i, p) for i, p in ch.get("lower_pts", [])[-2:]]

        return {
            "direction": direction,
            "entry": entry, "stop": stop, "target": target,
            "rr": rr, "support": support, "resistance": resistance,
            "anchors_up": anchors_up, "anchors_lo": anchors_lo,
        }

    # 遍历相邻收盘对
    for i in range(1, len(k5m) - 1):
        prev_c = float(k5m[i-1][4])
        curr_c = float(k5m[i][4])

        # 触碰下沿
        if lower - tol <= curr_c <= lower + tol and prev_c > lower + tol:
            key = f"channel_lower:{symbol}:{dk}"
            if not already_fired(state, key):
                mark_fired(state, key)
                sig = "做多关注" if ch["direction"] == "上升通道" else "反弹关注（**逆势，谨慎**）"
                meta = _build_trade_meta("lower", ch, k2h, current)
                return {
                    "symbol": symbol, "type": "触碰下沿", "channel": ch,
                    "price": current, "signal": sig, "meta": meta,
                }

    # 触碰上沿
    for i in range(1, len(k5m) - 1):
        prev_c = float(k5m[i-1][4])
        curr_c = float(k5m[i][4])
        if upper - tol <= curr_c <= upper + tol and prev_c < upper - tol:
            key = f"channel_upper:{symbol}:{dk}"
            if not already_fired(state, key):
                mark_fired(state, key)
                sig = "做空关注" if ch["direction"] == "下降通道" else "回落关注（**逆势，谨慎**）"
                meta = _build_trade_meta("upper", ch, k2h, current)
                return {
                    "symbol": symbol, "type": "触碰上沿", "channel": ch,
                    "price": current, "signal": sig, "meta": meta,
                }

    return None


# ==================== 反抽检测（Break & Retest） ====================

def check_retest(symbol, rules, state, dk, lookback_hours=24):
    """
    检测破位后反抽到关键位（滑动窗口 + 去重）：
      - 24 小时内 1H 收盘破位
      - 5m 收盘从破位反方向回到关键位附近
    """
    try:
        k1h = klines(symbol, "1h", lookback_hours + 2)
        k5m = klines(symbol, "5m", 8)
    except Exception as e:
        print(f"[retest:{symbol}] fetch error: {e}")
        return []
    if len(k1h) < 3 or len(k5m) < 3:
        return []

    current = float(k5m[-1][4])
    alerts = []

    for r in rules:
        level = r["level"]
        tol = level * 0.005

        # 查找 24h 内 1H K线是否有破位
        broke_down = False
        broke_up = False
        for i in range(len(k1h) - 1):
            o = float(k1h[i][1])
            c = float(k1h[i][4])
            if o > level and c < level - tol:
                broke_down = True
            elif o < level and c > level + tol:
                broke_up = True

        # 滑动窗口检测反抽
        for i in range(1, len(k5m) - 1):
            prev_c = float(k5m[i-1][4])
            curr_c = float(k5m[i][4])

            if broke_down and prev_c < level - tol and \
               (level - tol) <= curr_c <= (level + tol):
                key = f"retest_down:{symbol}:{level}:{dk}"
                if not already_fired(state, key):
                    mark_fired(state, key)
                    alerts.append({
                        "symbol": symbol, "level": level, "type": "反抽阻力",
                        "text": f"跌破 ${level:g} 后反抽 → **做空关注**",
                        "price": current, "icon": "🔻",
                    })

            if broke_up and prev_c > level + tol and \
               (level - tol) <= curr_c <= (level + tol):
                key = f"retest_up:{symbol}:{level}:{dk}"
                if not already_fired(state, key):
                    mark_fired(state, key)
                    alerts.append({
                        "symbol": symbol, "level": level, "type": "回踩支撑",
                        "text": f"突破 ${level:g} 后回踩 → **做多关注**",
                        "price": current, "icon": "🔺",
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


def check_ema_signals(symbol, now_utc, state, dk):
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
                    key = f"ema25:{symbol}:{dk}"
                    if not already_fired(state, key):
                        mark_fired(state, key)
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
                    key = f"ema100:{symbol}:{dk}"
                    if not already_fired(state, key):
                        mark_fired(state, key)
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
                    key = f"ema50d_up:{symbol}:{dk}"
                    if not already_fired(state, key):
                        mark_fired(state, key)
                        alerts.append({
                            "kind": "ema_cross",
                            "symbol": symbol,
                            "text": "🌟🌟 日线收盘上穿 EMA50（大趋势转多）",
                            "detail": f"收盘 ${close_d:.4f}  EMA50 ${e50:.4f}",
                            "icon": "🚀",
                        })
                elif prev_close_d > e50_prev and close_d <= e50:
                    key = f"ema50d_down:{symbol}:{dk}"
                    if not already_fired(state, key):
                        mark_fired(state, key)
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

    # 1. 关键位穿越（30 分钟滑动窗口 + 状态去重）
    state = _load_state()
    dk = today_key()
    for symbol, rules in RULES.items():
        try:
            # 拉最近 8 根 5m K线（40 分钟窗口）
            k = klines(symbol, "5m", 8)
            current = float(k[-1][4])  # 最新价（未收盘）
            # 遍历相邻收盘对 (i-1, i)，找首次穿越
            for i in range(1, len(k) - 1):  # 跳过 -1（未收盘），检查 [0..len-2]
                prev_c = float(k[i-1][4])
                curr_c = float(k[i][4])
                for r in rules:
                    lv = r["level"]
                    ddir = r["dir"]
                    key = f"level:{symbol}:{lv}:{ddir}:{dk}"
                    if ddir == "up" and prev_c < lv and curr_c >= lv:
                        if not already_fired(state, key):
                            mark_fired(state, key)
                            alerts.append(("level", symbol, current, r["desc"], "⬆️"))
                    elif ddir == "down" and prev_c > lv and curr_c <= lv:
                        if not already_fired(state, key):
                            mark_fired(state, key)
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
                vol_key = f"vol:{symbol}:{int(close_ts)}"
                if already_fired(state, vol_key):
                    continue
                change = (close_p - open_p) / open_p
                if abs(change) >= VOL_THRESHOLD:
                    mark_fired(state, vol_key)
                    arrow = "📈" if change > 0 else "📉"
                    alerts.append(("vol", symbol, close_p, f"4H 振幅 {change*100:+.2f}%", arrow))
        except Exception as e:
            print(f"[vol:{symbol}] error: {e}")

    # 3. 通道识别
    channel_alerts = []
    for symbol in CHANNEL_SYMBOLS:
        try:
            ca = check_channel(symbol, state, dk)
            if ca:
                channel_alerts.append(ca)
        except Exception as e:
            print(f"[channel:{symbol}] error: {e}")

    # 4. EMA 触碰 / 穿越
    ema_alerts = []
    for symbol in EMA_SYMBOLS:
        ema_alerts.extend(check_ema_signals(symbol, now_utc, state, dk))

    # 5. 破位反抽（对所有配了关键位的品种）
    retest_alerts = []
    for symbol, rules in RULES.items():
        retest_alerts.extend(check_retest(symbol, rules, state, dk))

    # 保存去重状态
    _save_state(state)

    if not alerts and not channel_alerts and not ema_alerts and not retest_alerts:
        print("No alerts triggered")
        return

    # 尝试加载 chart 模块（VPS 需要 apt install python3-matplotlib）
    try:
        import chart as chart_mod
        CHART_ENABLED = True
    except Exception as e:
        print(f"[chart] disabled: {e}")
        CHART_ENABLED = False

    stamp = now_utc.strftime("%m-%d %H:%M UTC")

    # === 通道信号：一个信号一张图 + 完整交易参数 ===
    for ca in channel_alerts:
        ch = ca["channel"]
        icon = "🔻" if ca["type"] == "触碰上沿" else "🔺"  # 上沿=做空(红)，下沿=做多(绿)
        m = ca.get("meta", {})
        direction_tag = m.get("direction", "?")
        entry = m.get("entry", ca["price"])
        stop = m.get("stop", 0)
        target = m.get("target", 0)
        rr = m.get("rr", 0)

        lines_c = []
        lines_c.append(f"{icon} *{ca['symbol']}* · {ca['type']} · **{direction_tag}**")
        lines_c.append("")
        lines_c.append(f"📐 结构：2H {ch['direction']}  |  触点 {ch['touch_upper']}/{ch['touch_lower']}  R² {ch['r2_upper']:.2f}/{ch['r2_lower']:.2f}")
        lines_c.append(f"💵 现价 `${ca['price']}`")
        lines_c.append(f"📐 通道上沿 / 下沿：`${ch['upper']:.4g}` / `${ch['lower']:.4g}`")
        lines_c.append("")
        lines_c.append(f"🎯 参考入场：`${entry:.4g}`")
        lines_c.append(f"🛑 参考止损：`${stop:.4g}`")
        lines_c.append(f"🎯 参考止盈：`${target:.4g}`  (R:R = {rr:.2f})")
        lines_c.append("")
        lines_c.append(f"🟢 近30根支撑：`${m.get('support', 0):.4g}`")
        lines_c.append(f"🔴 近30根压力：`${m.get('resistance', 0):.4g}`")
        anchors_up = m.get("anchors_up", [])
        anchors_lo = m.get("anchors_lo", [])
        if anchors_up:
            lines_c.append(f"📍 上沿锚点：{'  →  '.join(anchors_up)}")
        if anchors_lo:
            lines_c.append(f"📍 下沿锚点：{'  →  '.join(anchors_lo)}")
        lines_c.append("")
        lines_c.append(f"{ca['signal']}")
        lines_c.append(f"⚠️ 仅监控提醒，不自动下单")
        lines_c.append(f"_{stamp}_")
        caption = "\n".join(lines_c)
        sent = False
        if CHART_ENABLED:
            try:
                k2h = klines(ca["symbol"], "2h", 80)
                png = chart_mod.chart_channel(ca["symbol"], ch, k2h)
                push_all(caption, png)
                try: os.remove(png)
                except: pass
                sent = True
            except Exception as e:
                print(f"[chart_channel:{ca['symbol']}] {e}")
        if not sent:
            push_all(f"🔔 *交易信号*\n\n{caption}")

    # === EMA 信号：一个信号一张图 ===
    for ea in ema_alerts:
        caption = f"{ea['icon']} *{ea['symbol']}*  {ea['text']}\n{ea['detail']}\n\n_{stamp}_"
        sent = False
        if CHART_ENABLED and ea.get("kind") == "ema_touch":
            try:
                sym = ea["symbol"]
                k4h = klines(sym, "4h", 150)
                closes_x = [float(k[4]) for k in k4h]
                ema25s = _compute_ema(closes_x, 25) if len(closes_x) >= 25 else []
                ema100s = _compute_ema(closes_x, 100) if len(closes_x) >= 100 else []
                # 从 detail 里解析 EMA 值
                is_ema100 = "EMA100" in ea["text"]
                ema_name = "EMA100" if is_ema100 else "EMA25"
                ema_val = (ema100s[-2] if is_ema100 else ema25s[-2]) if (ema25s and ema100s) else 0
                png = chart_mod.chart_ema_touch(sym, k4h, ema25s, ema100s, closes_x[-2], ema_name, ema_val)
                push_all(caption, png)
                try: os.remove(png)
                except: pass
                sent = True
            except Exception as e:
                print(f"[chart_ema:{ea['symbol']}] {e}")
        if not sent:
            push_all(f"🔔 *交易信号*\n\n{caption}")

    # === 关键位穿越：一个信号一张图 ===
    for tup in alerts:
        kind, sym, price, desc, arrow = tup
        caption = f"{arrow} *{sym}*  `${price}`\n{desc}\n\n_{stamp}_"
        sent = False
        if CHART_ENABLED and kind == "level":
            try:
                # 从 desc 里 grep 价位数字比较麻烦，直接不画 level 图，保留纯文本
                pass
            except Exception as e:
                print(f"[chart_level:{sym}] {e}")
        push_all(f"🔔 *交易信号*\n\n{caption}")

    # === 反抽信号：带图 ===
    for ra in retest_alerts:
        caption = f"{ra['icon']} *{ra['symbol']}*  {ra['text']}\n现价 `${ra['price']}`  关键位 `${ra['level']:g}`\n\n_{stamp}_"
        sent = False
        if CHART_ENABLED:
            try:
                k5m = klines(ra["symbol"], "5m", 60)
                png = chart_mod.chart_level(ra["symbol"], k5m, ra["level"], ra["price"], desc=ra["type"])
                push_all(caption, png)
                try: os.remove(png)
                except: pass
                sent = True
            except Exception as e:
                print(f"[chart_retest:{ra['symbol']}] {e}")
        if not sent:
            push_all(f"🔔 *交易信号*\n\n{caption}")

    print(f"Sent: {len(alerts)} level/vol + {len(channel_alerts)} channel + {len(ema_alerts)} ema + {len(retest_alerts)} retest alerts")


def _compute_ema(values, period):
    """辅助：给主流程用的 EMA 计算（复用 ema_series）"""
    return ema_series(values, period)


if __name__ == "__main__":
    main()
