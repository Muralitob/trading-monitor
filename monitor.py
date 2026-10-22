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
# 可选：飞书群机器人 webhook URL 或 key
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "").strip()

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
    # 手动关键位：需要时再加，示例：
    # "HYPEUSDT": [
    #     {"level": 58.0, "dir": "up",   "desc": "..."},
    # ],
}

# 所有关注的品种
ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",   # 主流
    "HYPEUSDT", "XRPUSDT",             # 山寨大盘
    "ZECUSDT",                         # 隐私币
    "UNIUSDT", "AAVEUSDT",             # DeFi
    "XAUUSDT",                         # 黄金
]

VOLATILITY_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XAUUSDT"]
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


def send_feishu(text, title=None):
    """发送到飞书群机器人（text 用普通文本）"""
    if not FEISHU_WEBHOOK:
        return
    try:
        # 允许传完整 URL 或只传 key
        url = FEISHU_WEBHOOK if FEISHU_WEBHOOK.startswith("http") else \
              f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_WEBHOOK}"
        content = _strip_md(text)
        if title:
            content = f"[{title}]\n\n{content}"
        payload = {"msg_type": "text", "content": {"text": content}}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[feishu] {e}")


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

    # 飞书 / PushPlus / Bark 全部停用（用户 2026-08 决定：只走 Telegram）
    # 如需恢复飞书，取消下面 2 行注释
    # if FEISHU_WEBHOOK:
    #     send_feishu(caption, _extract_title(caption))


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


def check_channel(symbol, state, dk, timeframe="2h"):
    """检查 symbol 是否触碰通道边界，返回 alert dict 或 None（带状态去重）"""
    k2h = klines(symbol, timeframe, 200)
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
            key = f"channel_lower_{timeframe}:{symbol}:{dk}"
            if not already_fired(state, key):
                mark_fired(state, key)
                sig = "做多关注" if ch["direction"] == "上升通道" else "反弹关注（**逆势，谨慎**）"
                meta = _build_trade_meta("lower", ch, k2h, current)
                return {
                    "symbol": symbol, "type": "触碰下沿", "channel": ch,
                    "price": current, "signal": sig, "meta": meta,
                    "timeframe": timeframe, "k_bars": k2h,
                }

    # 触碰上沿
    for i in range(1, len(k5m) - 1):
        prev_c = float(k5m[i-1][4])
        curr_c = float(k5m[i][4])
        if upper - tol <= curr_c <= upper + tol and prev_c < upper - tol:
            key = f"channel_upper_{timeframe}:{symbol}:{dk}"
            if not already_fired(state, key):
                mark_fired(state, key)
                sig = "做空关注" if ch["direction"] == "下降通道" else "回落关注（**逆势，谨慎**）"
                meta = _build_trade_meta("upper", ch, k2h, current)
                return {
                    "symbol": symbol, "type": "触碰上沿", "channel": ch,
                    "price": current, "signal": sig, "meta": meta,
                    "timeframe": timeframe, "k_bars": k2h,
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


# ==================== 形态识别（三角/楔形/矩形）====================

def _classify_slope(slope, ref_price, n_bars):
    """按整段总位移百分比分类"""
    if ref_price == 0:
        return 'flat'
    total_move_pct = abs(slope * n_bars / ref_price)
    if total_move_pct < 0.015:  # 全段 < 1.5% ≈ 水平
        return 'flat'
    return 'up' if slope > 0 else 'down'


def _pattern_from_slopes(up_type, dn_type, us, ls):
    """按上下沿方向判定形态"""
    if up_type == 'flat' and dn_type == 'up':
        return ('上升三角', 'LONG', 'ASC-TRIANGLE')
    if up_type == 'down' and dn_type == 'flat':
        return ('下降三角', 'SHORT', 'DESC-TRIANGLE')
    if up_type == 'down' and dn_type == 'up':
        return ('对称三角', 'TREND', 'SYM-TRIANGLE')
    if up_type == 'flat' and dn_type == 'flat':
        return ('矩形', 'TREND', 'RECT')
    if up_type == 'up' and dn_type == 'up':
        # 上升楔形：下沿升得更快（收敛向上）→ 反转向下
        if ls > us * 1.3:
            return ('上升楔形', 'SHORT', 'RISING-WEDGE')
    if up_type == 'down' and dn_type == 'down':
        # 下降楔形：上沿跌得更快（收敛向下）→ 反转向上
        if us < ls * 1.3:
            return ('下降楔形', 'LONG', 'FALLING-WEDGE')
    return None


def detect_pattern(k4h):
    """在 4H K 线上找最佳形态。返回 dict 或 None"""
    if len(k4h) < 30:
        return None
    highs = [float(k[2]) for k in k4h]
    lows = [float(k[3]) for k in k4h]
    closes = [float(k[4]) for k in k4h]
    n = len(k4h)
    cur_price = closes[-1]

    best = None
    for window in (2, 3, 4):
        sh, sl = find_swings(highs, lows, window=window)
        for nu in (5, 4, 3):
            for nl in (5, 4, 3):
                if len(sh) < nu or len(sl) < nl:
                    continue
                upper_pts = sh[-nu:]
                lower_pts = sl[-nl:]
                all_idx = [p[0] for p in upper_pts + lower_pts]
                span = max(all_idx) - min(all_idx)
                if span < 15 or span > 50:
                    continue
                # 形态结束点必须在最近 5 根内
                if n - 1 - max(all_idx) > 5:
                    continue

                u = fit_line(upper_pts)
                l = fit_line(lower_pts)
                if not u or not l:
                    continue
                us, ui, ur2 = u
                ls, li, lr2 = l
                if ur2 < 0.65 or lr2 < 0.65:
                    continue

                up_type = _classify_slope(us, cur_price, span)
                dn_type = _classify_slope(ls, cur_price, span)
                pat = _pattern_from_slopes(up_type, dn_type, us, ls)
                if not pat:
                    continue

                cur_idx = n - 1
                upper_now = us * cur_idx + ui
                lower_now = ls * cur_idx + li
                if upper_now <= lower_now:
                    continue
                # 现价在形态附近
                if cur_price < lower_now * 0.9 or cur_price > upper_now * 1.1:
                    continue

                score = ur2 * lr2 * (len(upper_pts) + len(lower_pts))
                if best is None or score > best['_score']:
                    best = {
                        '_score': score,
                        'name_cn': pat[0],
                        'direction': pat[1],
                        'name_en': pat[2],
                        'upper': upper_now, 'lower': lower_now,
                        'slope_upper': us, 'slope_lower': ls,
                        'up_int': ui, 'lo_int': li,
                        'r2_upper': ur2, 'r2_lower': lr2,
                        'upper_pts': upper_pts, 'lower_pts': lower_pts,
                        'span': span,
                        'start_idx': min(all_idx),
                    }
    return best


def check_pattern_breakout(symbol, state, dk, timeframe="4h"):
    """检测形态突破。返回 alert dict 或 None"""
    try:
        k4h = klines(symbol, timeframe, 100)
    except Exception as e:
        print(f"[pattern:{symbol}:{timeframe}] fetch: {e}")
        return None
    if len(k4h) < 30:
        return None
    pat = detect_pattern(k4h)
    if not pat:
        return None

    # 用刚收盘的 4H 判断突破
    last = k4h[-2]
    prev = k4h[-3]
    c = float(last[4]); o = float(last[1])
    prev_c = float(prev[4])
    n_last = len(k4h) - 2  # last closed 的 index

    us = pat['slope_upper']; ui = pat['up_int']
    ls = pat['slope_lower']; li = pat['lo_int']
    upper_at = us * n_last + ui
    lower_at = ls * n_last + li
    body_pct = abs(c - o) / c if c else 0

    direction = pat['direction']
    # 对称三角/矩形 → 按前趋势判断
    if direction == 'TREND':
        start = pat['start_idx']
        pre_lo = max(0, start - 15)
        if start - pre_lo < 5:
            return None
        pre_closes = [float(k[4]) for k in k4h[pre_lo:start]]
        direction = 'LONG' if pre_closes[-1] > pre_closes[0] else 'SHORT'

    # 突破判定
    signal = False
    if direction == 'LONG':
        if c > upper_at and prev_c <= upper_at * 1.002 and body_pct >= 0.004:
            signal = True
    else:
        if c < lower_at and prev_c >= lower_at * 0.998 and body_pct >= 0.004:
            signal = True

    if not signal:
        return None

    tf_up = timeframe.upper()
    key = f"pattern:{tf_up}:{pat['name_en']}:{symbol}:{dk}"
    if already_fired(state, key):
        return None
    mark_fired(state, key)

    return {
        'symbol': symbol,
        'kind': 'pattern_break',
        'timeframe': tf_up,
        'pattern_cn': pat['name_cn'],
        'pattern_en': pat['name_en'],
        'direction': direction,
        'price': c,
        'upper': upper_at,
        'lower': lower_at,
        'pattern_data': pat,
        'text': f"{tf_up} {pat['name_cn']} 突破 → {'做多' if direction == 'LONG' else '做空'}",
    }


# ==================== 支撑/压力翻转（S/R Flip）====================

def _find_swings_simple(highs, lows, window=3):
    """返回 [(idx, price, 'H'|'L')] 按 idx 升序"""
    out = []
    for i in range(window, len(highs) - window):
        if all(highs[i] >= highs[i + j] for j in range(-window, window + 1) if j != 0):
            out.append((i, highs[i], "H"))
        if all(lows[i] <= lows[i + j] for j in range(-window, window + 1) if j != 0):
            out.append((i, lows[i], "L"))
    return out


def check_sr_flip(symbol, state, dk):
    """
    自动 S/R Flip 检测（4H swing）：
      - 找最近 60 根 4H 的 swing high / low
      - swing high 若被 4H 收盘上破（且之后≥2根维持），成为"翻转支撑"
      - 现价（5m）从上方回来试探该位置（±0.5%）→ LONG 机会
      - 反过来同理 → SHORT
    """
    try:
        k4h = klines(symbol, "4h", 80)
        k5m = klines(symbol, "5m", 8)
    except Exception as e:
        print(f"[srflip:{symbol}] fetch: {e}")
        return []
    if len(k4h) < 30 or len(k5m) < 3:
        return []

    highs = [float(k[2]) for k in k4h]
    lows = [float(k[3]) for k in k4h]
    closes = [float(k[4]) for k in k4h]
    current = float(k5m[-1][4])

    # 用 window=3 找 swing（近 60 根）
    swings = _find_swings_simple(highs, lows, window=3)
    if not swings:
        return []

    alerts = []
    last_idx = len(closes) - 1

    # 只看最近 15 根内的 swing 突破（避免太老的价位）
    for idx, level, kind in swings:
        # 需要至少 3 根 K 线之后来判断突破
        if idx > last_idx - 3:
            continue
        tol = level * 0.005

        if kind == "H":
            # 检查是否被向上突破：swing 之后有 K 线收盘 > level + tol
            broke_up_idx = None
            for j in range(idx + 1, last_idx + 1):
                if closes[j] > level + tol:
                    broke_up_idx = j
                    break
            if broke_up_idx is None:
                continue
            # 突破后至少 2 根维持在上方
            stayed = sum(1 for j in range(broke_up_idx, last_idx + 1) if closes[j] > level - tol)
            if stayed < 2:
                continue
            # 突破点到现在不能太老（<= 12 根 4H = 2 天内）
            if last_idx - broke_up_idx > 12:
                continue
            # 现价是否在回踩：从上方回到 level±tol
            if level - tol <= current <= level + tol and closes[-1] > level:
                key = f"srflip_up:{symbol}:{round(level, 6)}:{dk}"
                if not already_fired(state, key):
                    mark_fired(state, key)
                    alerts.append({
                        "symbol": symbol, "kind": "sr_flip",
                        "direction": "LONG",
                        "level": level, "price": current,
                        "text": f"4H 突破前压力 ${level:.4g} 后回踩 → 压力翻转支撑",
                        "detail": f"突破点 {last_idx - broke_up_idx} 根前，之后 {stayed} 根维持上方",
                    })
        else:
            # swing low：被向下跌破 → 支撑翻转压力
            broke_dn_idx = None
            for j in range(idx + 1, last_idx + 1):
                if closes[j] < level - tol:
                    broke_dn_idx = j
                    break
            if broke_dn_idx is None:
                continue
            stayed = sum(1 for j in range(broke_dn_idx, last_idx + 1) if closes[j] < level + tol)
            if stayed < 2:
                continue
            if last_idx - broke_dn_idx > 12:
                continue
            if level - tol <= current <= level + tol and closes[-1] < level:
                key = f"srflip_dn:{symbol}:{round(level, 6)}:{dk}"
                if not already_fired(state, key):
                    mark_fired(state, key)
                    alerts.append({
                        "symbol": symbol, "kind": "sr_flip",
                        "direction": "SHORT",
                        "level": level, "price": current,
                        "text": f"4H 跌破前支撑 ${level:.4g} 后反抽 → 支撑翻转压力",
                        "detail": f"跌破点 {last_idx - broke_dn_idx} 根前，之后 {stayed} 根维持下方",
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


def _check_ema_rejection(k_bar, ema_val, min_wick_ratio=1.0):
    """
    检测 EMA 拒绝形态（碰了但被拒绝）
    返回 ("SHORT", detail) / ("LONG", detail) / None
    """
    o = float(k_bar[1]); h = float(k_bar[2]); l = float(k_bar[3]); c = float(k_bar[4])
    body_top = max(o, c)
    body_bottom = min(o, c)
    body = abs(c - o) or 0.0001

    # SHORT 拒绝：冲上 EMA 后 K线主体回落到 EMA 下方
    if h >= ema_val and body_top < ema_val:
        upper_wick = h - body_top
        if upper_wick >= body * min_wick_ratio:
            return "SHORT", {"wick_ratio": upper_wick / body}

    # LONG 反弹：下探 EMA 后 K线主体弹回 EMA 上方
    if l <= ema_val and body_bottom > ema_val:
        lower_wick = body_bottom - l
        if lower_wick >= body * min_wick_ratio:
            return "LONG", {"wick_ratio": lower_wick / body}

    return None, None


def _check_ema_break(k_bar, prev_bar, ema_now, ema_prev, min_body_pct=0.003):
    """
    检测 EMA 突破形态（真的穿过去了）
    返回 ("UP", detail) / ("DOWN", detail) / None

    UP 突破（做多）：
      - 上一根收盘 < 上一根 EMA
      - 本根收盘 > 本根 EMA
      - 本根实体 >= min_body_pct（避免小实体假突破）
      - 本根收盘 > EMA 至少 0.1%（不是刚好压线）

    DOWN 突破（做空）：
      - 反向逻辑
    """
    o = float(k_bar[1]); c = float(k_bar[4])
    prev_c = float(prev_bar[4])
    body = abs(c - o)
    body_pct = body / c if c else 0

    # 上穿
    if prev_c < ema_prev and c > ema_now:
        # 实体够大 + 明确站上 EMA
        if body_pct >= min_body_pct and (c - ema_now) / ema_now >= 0.001:
            return "UP", {
                "close": c, "ema": ema_now,
                "dist_pct": (c - ema_now) / ema_now * 100,
                "body_pct": body_pct * 100,
            }

    # 下破
    if prev_c > ema_prev and c < ema_now:
        if body_pct >= min_body_pct and (ema_now - c) / ema_now >= 0.001:
            return "DOWN", {
                "close": c, "ema": ema_now,
                "dist_pct": (ema_now - c) / ema_now * 100,
                "body_pct": body_pct * 100,
            }

    return None, None


def _scan_emas_on_bar(k_bars, ema_periods, is_daily=False):
    """
    在一根 K 线上扫描所有 EMA 的拒绝/突破形态
    返回按 (action, direction) 分组的字典：
      {"reject_SHORT": [(name, star, ema_val, meta), ...],
       "reject_LONG":  [...],
       "break_UP":     [...],
       "break_DOWN":   [...]}
    """
    last_bar = k_bars[-2]
    prev_bar = k_bars[-3]
    closes = [float(k[4]) for k in k_bars]

    groups = {"reject_SHORT": [], "reject_LONG": [], "break_UP": [], "break_DOWN": []}

    for period, star, name in ema_periods:
        if len(closes) < period + 5:
            continue
        ema = ema_series(closes, period)
        e_now = ema[-2]
        e_prev = ema[-3]

        # 拒绝
        rd, rm = _check_ema_rejection(last_bar, e_now, min_wick_ratio=1.0)
        if rd:
            groups[f"reject_{rd}"].append((name, star, e_now, rm))

        # 突破
        bd, bm = _check_ema_break(last_bar, prev_bar, e_now, e_prev)
        if bd:
            groups[f"break_{bd}"].append((name, star, e_now, bm))

    return groups, last_bar


def _emit_ema_group(symbol, group_hits, group_key, timeframe, is_daily, last_bar, state, dk):
    """
    把一个 group（如 reject_SHORT）里所有 EMA 合并成一条 alert
    """
    if not group_hits:
        return None

    action, direction = group_key.split("_")  # "reject","SHORT"
    # 用最重要（最大 period）的 EMA 作为主 key，避免同事件反复推
    main_name = group_hits[-1][0]  # 最后一个 = 最大 period（EMA_CONFIG 是升序）
    star_max = ""
    for _, s, _, _ in group_hits:
        if len(s) > len(star_max):
            star_max = s

    if is_daily:
        star_display = (star_max + "🌟") if star_max else "🌟"
    else:
        star_display = star_max

    key = f"ema_{action}_{timeframe}:{group_key}:{symbol}:{dk}"
    if already_fired(state, key):
        return None
    mark_fired(state, key)

    # 描述
    if action == "reject":
        dir_zh = "拒绝上方（做空）" if direction == "SHORT" else "反弹上方（做多）"
    else:
        dir_zh = "上穿（做多）" if direction == "UP" else "下破（做空）"

    close_p = float(last_bar[4])
    low = float(last_bar[3])
    high = float(last_bar[2])

    ema_list = " / ".join(n for n, _, _, _ in group_hits)
    ema_vals = "  ".join(f"{n} ${v:.4g}" for n, _, v, _ in group_hits)

    if action == "reject":
        avg_wick = sum(m["wick_ratio"] for _, _, _, m in group_hits) / len(group_hits)
        icon = "📊" if not is_daily else "🚀"
        text = f"{star_display + ' ' if star_display else ''}{timeframe.upper()} {dir_zh} · {ema_list}"
        detail = (f"收盘 ${close_p:.4f}\n"
                  f"{ema_vals}\n"
                  f"影线 ${low:.4f}-${high:.4f}  拒绝比 {avg_wick:.2f}")
    else:  # break
        avg_dist = sum(m["dist_pct"] for _, _, _, m in group_hits) / len(group_hits)
        arrow = "🚀" if direction == "UP" else "💥"
        icon = arrow
        text = f"{star_display + ' ' if star_display else ''}{arrow} {timeframe.upper()} {dir_zh} · {ema_list}"
        detail = (f"收盘 ${close_p:.4f}\n"
                  f"{ema_vals}\n"
                  f"平均距 EMA {avg_dist:+.2f}%")

    real_direction = direction if action == "reject" else ("LONG" if direction == "UP" else "SHORT")
    return {
        "kind": f"ema_{action}", "symbol": symbol,
        "direction": real_direction,
        "text": text, "detail": detail, "icon": icon,
    }


def check_ema_signals(symbol, now_utc, state, dk):
    """
    EMA 拒绝/突破检测（4H + 1D）
    同品种同周期同方向的多条 EMA 合并推送为一条
    """
    now_ts = now_utc.timestamp()
    alerts = []

    EMA_CONFIG = [
        (12,  "",     "EMA12"),
        (21,  "",     "EMA21"),
        (52,  "🌟",   "EMA52"),
        (100, "🌟",   "EMA100"),
        (200, "🌟🌟", "EMA200"),
    ]

    # ==== 4H ====
    try:
        k4h = klines(symbol, "4h", 250)
        if len(k4h) >= 3 and _bar_just_closed(k4h[-2], now_ts, tolerance_sec=480):
            groups, last_bar = _scan_emas_on_bar(k4h, EMA_CONFIG, is_daily=False)
            for gk, hits in groups.items():
                alert = _emit_ema_group(symbol, hits, gk, "4h", False, last_bar, state, dk)
                if alert:
                    alerts.append(alert)
    except Exception as e:
        print(f"[ema_4h:{symbol}] {e}")

    # ==== 1D ====
    try:
        k1d = klines(symbol, "1d", 250)
        if len(k1d) >= 3 and _bar_just_closed(k1d[-2], now_ts, tolerance_sec=900):
            groups, last_bar = _scan_emas_on_bar(k1d, EMA_CONFIG, is_daily=True)
            for gk, hits in groups.items():
                alert = _emit_ema_group(symbol, hits, gk, "1d", True, last_bar, state, dk)
                if alert:
                    alerts.append(alert)
    except Exception as e:
        print(f"[ema_1d:{symbol}] {e}")

    return alerts


# ==================== 主流程 ====================

def main():
    now_utc = datetime.datetime.utcnow()
    bj_hour = (now_utc.hour + 8) % 24
    bj_min = now_utc.minute

    if 0 <= bj_hour < 7:
        print(f"Quiet hours (BJ {bj_hour:02d}:xx), skipping")
        return

    state = _load_state()
    dk = today_key()

    # 心跳：每 4 小时（BJ 8/12/16/20 点整分）发一次监控状态
    if bj_hour in (8, 12, 16, 20) and bj_min == 0:
        hb_key = f"heartbeat:{dk}:{bj_hour}"
        if not already_fired(state, hb_key):
            mark_fired(state, hb_key)
            try:
                send_tg(f"💓 监控心跳 · BJ {bj_hour}:00 · 系统运行中")
            except Exception as e:
                print(f"[heartbeat] {e}")
            _save_state(state)

    alerts = []

    # 1. 关键位穿越（30 分钟滑动窗口 + 状态去重）
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

    # 3. 通道识别（只跑 4H）
    channel_alerts = []
    for symbol in CHANNEL_SYMBOLS:
        try:
            ca = check_channel(symbol, state, dk, timeframe="4h")
            if ca:
                channel_alerts.append(ca)
        except Exception as e:
            print(f"[channel:{symbol}:4h] error: {e}")

    # 4. EMA 触碰 / 穿越
    ema_alerts = []
    for symbol in EMA_SYMBOLS:
        ema_alerts.extend(check_ema_signals(symbol, now_utc, state, dk))

    # 5. 破位反抽（对所有配了关键位的品种）
    retest_alerts = []
    for symbol, rules in RULES.items():
        retest_alerts.extend(check_retest(symbol, rules, state, dk))

    # 6. S/R Flip 自动检测（4H swing）
    srflip_alerts = []
    for symbol in ALL_SYMBOLS:
        try:
            srflip_alerts.extend(check_sr_flip(symbol, state, dk))
        except Exception as e:
            print(f"[srflip:{symbol}] {e}")

    # 7. 形态突破（1H 短线 + 4H 主）
    pattern_alerts = []
    for symbol in ALL_SYMBOLS:
        for tf in ("1h", "4h"):
            try:
                pa = check_pattern_breakout(symbol, state, dk, timeframe=tf)
                if pa:
                    pattern_alerts.append(pa)
            except Exception as e:
                print(f"[pattern:{symbol}:{tf}] {e}")

    # 8. 震荡切换提醒：4H 收盘后从趋势进入震荡 / 震荡结束，单独推一条
    for symbol in ALL_SYMBOLS:
        try:
            check_regime_switch(symbol, state)
        except Exception as e:
            print(f"[regime:{symbol}] {e}")

    # 保存去重状态
    _save_state(state)

    if not alerts and not channel_alerts and not ema_alerts and not retest_alerts and not srflip_alerts and not pattern_alerts:
        print("No alerts triggered")
        return

    # ============================================================
    # 关键改造：不再单独推每个指标，而是按品种/方向汇总打分
    # 达阈值才推 → 一条完整"交易机会"消息
    # ============================================================
    from collections import defaultdict
    scoreboards = defaultdict(lambda: {"score": 0, "reasons": [], "meta": {}, "hits": []})

    # 关键位穿越 (weight 25)
    for kind, sym, price, desc, arrow in alerts:
        if kind == "level":
            direction = "LONG" if arrow == "⬆️" else "SHORT"
            board = scoreboards[(sym, direction)]
            board["score"] += 25
            board["reasons"].append(f"关键位穿越 {desc}")
            board["hits"].append(("level", desc))

    # 通道触碰 (4H, weight 45)
    for ca in channel_alerts:
        meta = ca.get("meta", {})
        direction = meta.get("direction", "?")
        if direction not in ("LONG", "SHORT"): continue
        tf = ca.get("timeframe", "4h").upper()
        board = scoreboards[(ca["symbol"], direction)]
        board["score"] += 45
        board["reasons"].append(f"{tf} 通道 · {ca['type']}（{ca['channel']['direction']}）")
        board["hits"].append(("channel", ca))
        if "channel_meta" not in board["meta"]:
            board["meta"]["channel_meta"] = meta
            board["meta"]["channel_ch"] = ca["channel"]
            board["meta"]["channel_price"] = ca["price"]
            board["meta"]["channel_tf"] = tf

    # EMA 拒绝/突破
    for ea in ema_alerts:
        direction = ea["direction"]
        if direction not in ("LONG", "SHORT"): continue
        # 权重：4H 无星=15，🌟=25；1D 无星=25，🌟=40，🌟🌟=50
        star = ea.get("text", "")
        is_1d = "1D" in star or "1d" in star.lower()
        star_count = star.count("🌟")
        if is_1d:
            weight = {0: 25, 1: 40, 2: 50, 3: 60}.get(star_count, 25)
        else:
            weight = {0: 15, 1: 25, 2: 35}.get(star_count, 15)
        board = scoreboards[(ea["symbol"], direction)]
        board["score"] += weight
        board["reasons"].append(ea["text"])
        board["hits"].append(("ema", ea))

    # 形态突破 (4H=50 主力,1H=30 短线)
    for pa in pattern_alerts:
        direction = pa["direction"]
        tf = pa.get("timeframe", "4H")
        weight = 30 if tf == "1H" else 50
        board = scoreboards[(pa["symbol"], direction)]
        board["score"] += weight
        board["reasons"].append(pa["text"])
        board["hits"].append(("pattern", pa))

    # S/R Flip 自动检测 (weight 40：跟通道差不多重)
    for sf in srflip_alerts:
        direction = sf["direction"]
        board = scoreboards[(sf["symbol"], direction)]
        board["score"] += 40
        board["reasons"].append(sf["text"])
        board["hits"].append(("srflip", sf))

    # 破位反抽 (weight 30)
    for ra in retest_alerts:
        direction = "SHORT" if "反抽" in ra["type"] else "LONG"
        board = scoreboards[(ra["symbol"], direction)]
        board["score"] += 30
        board["reasons"].append(f"破位后 {ra['type']}（$${ra['level']}）")
        board["hits"].append(("retest", ra))

    # 大盘异动只当"背景"（不参与开单判断）
    market_context = []
    for kind, sym, price, desc, arrow in alerts:
        if kind == "vol":
            market_context.append(f"{arrow} {sym}: {desc}")

    # 阈值 45 分（要求共振）+ 每次最多推 top 2
    THRESHOLD = 45
    MAX_PUSH = 2
    opportunities = [
        (key, board) for key, board in scoreboards.items()
        if board["score"] >= THRESHOLD
    ]
    opportunities.sort(key=lambda x: -x[1]["score"])
    opportunities = opportunities[:MAX_PUSH]

    if not opportunities:
        print(f"No opportunity: {len(scoreboards)} signals but max score below {THRESHOLD}")
        return

    # 加载图表模块
    try:
        import chart as chart_mod
        CHART_ENABLED = True
    except Exception as e:
        print(f"[chart] disabled: {e}")
        CHART_ENABLED = False

    stamp = now_utc.strftime("%m-%d %H:%M UTC")

    # 推送每个交易机会
    for (sym, direction), board in opportunities:
        _push_opportunity(sym, direction, board, market_context, stamp, chart_mod if CHART_ENABLED else None)

    print(f"Pushed {len(opportunities)} opportunities")
    return  # 早返回，不再走后面单独推送逻辑

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


def check_regime_switch(symbol, state):
    """比较最近两根已收盘 4H 的行情状态，进入/离开震荡时推送（每根 K 线只推一次）"""
    import regime
    k = klines(symbol, "4h", 170)[:-1]
    now_r = regime.classify(k)
    states = regime.chop_states(k)
    was_chop, is_chop = states[-2], states[-1]
    if was_chop == is_chop:
        return
    key = f"regime:{symbol}:{k[-1][6] // 1000}"
    if already_fired(state, key):
        return
    mark_fired(state, key)
    now_r["tf"] = "4h"
    if is_chop:
        push_all(f"🟡 *{symbol}* 4H 进入震荡行情\n\n{regime.warning_text(now_r)}\n\n"
                 f"提醒自己：震荡里亏钱最多的是 追突破 + 区间中间开单")
    else:
        push_all(f"{regime.icon(now_r['regime'])} *{symbol}* 4H 震荡结束 → {now_r['regime']}\n"
                 f"均线方向：{now_r['trend_dir']}  ADX {now_r['adx']:.0f}  效率比 {now_r['er']:.2f}\n"
                 f"还是要等结构+价格行为确认再进")


def _compute_ema(values, period):
    """辅助：给主流程用的 EMA 计算（复用 ema_series）"""
    return ema_series(values, period)


def _build_narrative(sym, direction, hits):
    """
    把多个指标 hits 拼成"故事"，解释为什么这是个机会
    例：4H 下降通道跌破后又回到通道内 → 假破位反弹信号 → 1H 收阳线确认 → 做多机会
    """
    parts = []

    # 分类各种 hit
    channel_hit = None
    ema_hits = []
    level_hits = []
    retest_hits = []
    srflip_hits = []
    pattern_hits = []

    for kind, payload in hits:
        if kind == "channel":
            channel_hit = payload
        elif kind == "ema":
            ema_hits.append(payload)
        elif kind == "level":
            level_hits.append(payload)
        elif kind == "retest":
            retest_hits.append(payload)
        elif kind == "srflip":
            srflip_hits.append(payload)
        elif kind == "pattern":
            pattern_hits.append(payload)

    dir_zh_action = "做多" if direction == "LONG" else "做空"

    # === 1. 通道故事 ===
    if channel_hit:
        ch = channel_hit["channel"]
        ch_dir = ch["direction"]
        touch_type = channel_hit["type"]
        tf = channel_hit.get("timeframe", "2h").upper()
        # 通道方向 + 触碰位置 + 顺逆势判断
        if direction == "LONG" and touch_type == "触碰下沿":
            if ch_dir == "上升通道":
                parts.append(f"📐 **{tf} 上升通道**跌到**下沿**后止跌，属**顺势回调支撑**。")
            elif ch_dir == "下降通道":
                parts.append(f"📐 **{tf} 下降通道**跌到下沿虽然逆势，但**触点 {ch['touch_lower']} 次**说明该位置有效，可能出现反弹。")
            else:
                parts.append(f"📐 **{tf} 震荡区间**下沿附近，箱底做多。")
        elif direction == "SHORT" and touch_type == "触碰上沿":
            if ch_dir == "下降通道":
                parts.append(f"📐 **{tf} 下降通道**冲高到**上沿**遇阻，**顺势做空首选**。")
            elif ch_dir == "上升通道":
                parts.append(f"📐 **{tf} 上升通道**冲到上沿，虽然逆势，但触点 {ch['touch_upper']} 次说明该位置有效，警惕短线回落。")
            else:
                parts.append(f"📐 **{tf} 震荡区间**上沿附近，箱顶做空。")

    # === 2. EMA 故事 ===
    if ema_hits:
        # 按周期分组
        h4_reject = [e for e in ema_hits if "4H" in e.get("text", "") and e.get("kind") == "ema_reject"]
        h4_break = [e for e in ema_hits if "4H" in e.get("text", "") and e.get("kind") == "ema_break"]
        d1_reject = [e for e in ema_hits if "1D" in e.get("text", "") and e.get("kind") == "ema_reject"]
        d1_break = [e for e in ema_hits if "1D" in e.get("text", "") and e.get("kind") == "ema_break"]

        # 日线突破是最大信号
        if d1_break:
            ema_names = " / ".join(_extract_ema_names(e) for e in d1_break)
            action_zh = "站上" if direction == "LONG" else "跌破"
            parts.append(f"🚀 **日线**已经{action_zh} **{ema_names}**（K 线主体真的穿过去了，不是插针），这是**大级别趋势转折**的确认。")

        # 日线拒绝
        if d1_reject:
            ema_names = " / ".join(_extract_ema_names(e) for e in d1_reject)
            action_zh = "反弹上方" if direction == "LONG" else "冲高受阻"
            parts.append(f"🌟 **日线**在 **{ema_names}** 位置{action_zh}，K 线主体收回来了 → **大级别位置被守住**。")

        # 4H 突破
        if h4_break:
            ema_names = " / ".join(_extract_ema_names(e) for e in h4_break)
            action_zh = "站上" if direction == "LONG" else "跌破"
            parts.append(f"⚡ **4H 已经{action_zh} {ema_names}**（主体真穿越），短期动能确认。")

        # 4H 拒绝
        if h4_reject:
            ema_names = " / ".join(_extract_ema_names(e) for e in h4_reject)
            if direction == "LONG":
                parts.append(f"📊 **4H 下探 {ema_names} 后主体收回上方**，说明多头在这个位置进场护盘。")
            else:
                parts.append(f"📊 **4H 冲高触到 {ema_names} 后主体被压回下方**，说明空头在这个位置卖压强。")

    # === 3. 关键位穿越 ===
    if level_hits:
        for lh in level_hits:
            parts.append(f"🎯 关键位穿越：{lh}")

    # === 形态突破 ===
    for ph in pattern_hits:
        pd = ph["pattern_data"]
        side = "向上" if ph["direction"] == "LONG" else "向下"
        tf = ph.get("timeframe", "4H")
        parts.append(f"🔺 **{tf} {ph['pattern_cn']}** 形态识别成功（{pd['span']} 根 K 线，R² {pd['r2_upper']:.2f}/{pd['r2_lower']:.2f}），"
                     f"刚刚**{side}收盘突破** `${ph['price']:.4g}`，上沿 `${ph['upper']:.4g}` / 下沿 `${ph['lower']:.4g}`。")

    # === S/R 翻转（自动） ===
    for sf in srflip_hits:
        if sf["direction"] == "LONG":
            parts.append(f"🔄 **4H 突破前压力 ${sf['level']:.4g} 后回踩** → 压力已经翻转成支撑，回来试探成功=**做多**。\n   _{sf['detail']}_")
        else:
            parts.append(f"🔄 **4H 跌破前支撑 ${sf['level']:.4g} 后反抽** → 支撑已经翻转成压力，反抽到位=**做空**。\n   _{sf['detail']}_")

    # === 4. 破位反抽 ===
    if retest_hits:
        for rh in retest_hits:
            parts.append(f"🔻 破位反抽：{rh['type']} @ ${rh['level']}（先跌破再反弹到原支撑位试探）")

    return "\n\n".join(parts)


def _extract_ema_names(hit):
    """从 hit 的 text 里抠出 EMA 名字（如 'EMA21 / EMA52'）"""
    import re
    text = hit.get("text", "")
    m = re.findall(r"EMA\d+", text)
    if m:
        return " / ".join(m)
    return "EMA"


def _check_1h_confirmation(sym, direction):
    """
    1H 确认：最近一根 1H K 线方向是否支持 direction
    返回 str 描述或空字符串
    """
    try:
        k1h = klines(sym, "1h", 3)
        last = k1h[-2]  # 已收盘
        o = float(last[1]); c = float(last[4])
        if direction == "LONG" and c > o:
            change = (c - o) / o * 100
            return f"✅ 1H 最新收盘 **阳线** ({change:+.2f}%)，方向与做多一致。"
        elif direction == "SHORT" and c < o:
            change = (c - o) / o * 100
            return f"✅ 1H 最新收盘 **阴线** ({change:.2f}%)，方向与做空一致。"
        else:
            return f"⚠️ 1H 最新收盘方向与 {direction} 不一致，等更明确的 1H 反转再动手。"
    except Exception:
        return ""


def _push_opportunity(sym, direction, board, market_context, stamp, chart_mod):
    """
    把一个共振交易机会推送到 Telegram（附完整策略参数 + 逻辑故事）
    """
    score = board["score"]
    hits = board["hits"]
    reasons = board["reasons"]
    meta = board["meta"]

    # 分档
    if score >= 80:
        tier_icon = "🌟🌟🌟"
        tier_zh = "高质量共振"
    elif score >= 60:
        tier_icon = "🌟🌟"
        tier_zh = "多指标共振"
    else:
        tier_icon = "🌟"
        tier_zh = "单指标信号"

    dir_icon = "🟢" if direction == "LONG" else "🔴"
    dir_zh = "做多 LONG" if direction == "LONG" else "做空 SHORT"

    # ===== 计算策略参数 =====
    # 优先用通道给的参数（通道逻辑已经很好了）
    entry = stop = target = rr = None
    if meta.get("channel_meta"):
        cm = meta["channel_meta"]
        entry = cm["entry"]; stop = cm["stop"]; target = cm["target"]; rr = cm["rr"]
        strategy_source = "通道结构"
    else:
        # 智能算：止损=近 3 根极值 + 缓冲；目标=最近的 EMA/前高低点
        try:
            k4h = klines(sym, "4h", 60)
            highs = [float(k[2]) for k in k4h]
            lows = [float(k[3]) for k in k4h]
            closes_x = [float(k[4]) for k in k4h]
            current = closes_x[-1]
            entry = current

            # 计算多条 EMA 值
            ema_vals = {}
            for p in [21, 52, 100, 200]:
                if len(closes_x) >= p + 3:
                    ema_vals[p] = ema_series(closes_x, p)[-1]

            if direction == "LONG":
                # 止损：最近 3 根 K 线最低点 - 0.3%
                stop = min(lows[-3:]) * 0.997
                # 目标：找上方最近的目标
                candidates_above = []
                for v in ema_vals.values():
                    if v > current * 1.005:
                        candidates_above.append(v)
                recent_high = max(highs[-30:])
                if recent_high > current * 1.005:
                    candidates_above.append(recent_high)
                target = min(candidates_above) if candidates_above else current * 1.03
            else:  # SHORT
                stop = max(highs[-3:]) * 1.003
                candidates_below = []
                for v in ema_vals.values():
                    if v < current * 0.995:
                        candidates_below.append(v)
                recent_low = min(lows[-30:])
                if recent_low < current * 0.995:
                    candidates_below.append(recent_low)
                target = max(candidates_below) if candidates_below else current * 0.97

            risk = abs(entry - stop)
            reward = abs(target - entry)
            rr = reward / risk if risk > 0 else 0
            strategy_source = "近3根止损 + 最近目标"
        except Exception as e:
            print(f"[strategy:{sym}] {e}")
            strategy_source = "无法计算"

    # R:R 不再强制过滤 — 只在消息里标 ⚠️ 让你自己看图判断

    # ===== 组装消息 =====
    lines = [f"{tier_icon} **{sym}** · {dir_icon} **{dir_zh}** · 共振 {score} 分（{tier_zh}）"]
    lines.append("")

    # === 震荡警告（放最前面，先看到）===
    try:
        import regime
        warn = regime.warning_text(regime.get_regime(sym, "4h"), direction)
        if warn:
            lines.append(warn)
            lines.append("")
    except Exception as e:
        print(f"[regime:{sym}] {e}")

    # === 完整逻辑故事 ===
    narrative = _build_narrative(sym, direction, hits)
    if narrative:
        lines.append("🧠 **交易逻辑**")
        lines.append(narrative)
        lines.append("")

    # === 1H 确认 ===
    confirmation = _check_1h_confirmation(sym, direction)
    if confirmation:
        lines.append(confirmation)
        lines.append("")

    # === 当前市场状态 ===
    try:
        k4h = klines(sym, "4h", 30)
        highs = [float(k[2]) for k in k4h]
        lows = [float(k[3]) for k in k4h]
        closes_4h = [float(k[4]) for k in k4h]
        current_price = closes_4h[-1]
        support = min(lows[-30:])
        resistance = max(highs[-30:])

        lines.append("📊 **当前状态**")
        lines.append(f"  💵 现价：`${current_price:.4g}`")
        lines.append(f"  🟢 近30根支撑：`${support:.4g}`")
        lines.append(f"  🔴 近30根压力：`${resistance:.4g}`")

        # 计算 4H EMA 距离
        ema_lines = []
        for p, name in [(21, 'EMA21'), (52, 'EMA52'), (100, 'EMA100')]:
            if len(closes_4h) >= p + 3:
                ev = ema_series(closes_4h, p)[-1]
                dist = (current_price - ev) / ev * 100
                icon = "↑" if dist > 0 else "↓"
                ema_lines.append(f"{name} `${ev:.4g}` {icon}{dist:+.2f}%")
        if ema_lines:
            lines.append(f"  📉 4H：{' · '.join(ema_lines)}")
        lines.append("")
    except Exception as e:
        print(f"[status:{sym}] {e}")

    # === 通道详情（如果有）===
    if meta.get("channel_ch"):
        ch = meta["channel_ch"]
        cm = meta.get("channel_meta", {})
        lines.append("📐 **通道结构**")
        lines.append(f"  方向：{ch['direction']}  |  触点 {ch['touch_upper']}/{ch['touch_lower']}  |  R² {ch['r2_upper']:.2f}/{ch['r2_lower']:.2f}")
        lines.append(f"  上沿 `${ch['upper']:.4g}` / 下沿 `${ch['lower']:.4g}`")
        anchors_up = cm.get("anchors_up", [])
        anchors_lo = cm.get("anchors_lo", [])
        if anchors_up:
            lines.append(f"  📍 上沿锚点：{'  →  '.join(anchors_up)}")
        if anchors_lo:
            lines.append(f"  📍 下沿锚点：{'  →  '.join(anchors_lo)}")
        lines.append("")

    # 策略参数
    if entry and stop and target:
        lines.append(f"🎯 **策略参数**（{strategy_source}）")
        lines.append(f"  入场：`${entry:.4g}`")
        lines.append(f"  止损：`${stop:.4g}`")
        lines.append(f"  目标：`${target:.4g}`")
        # RR 染色
        rr_str = f"R:R = {rr:.2f}"
        if rr < 1.5:
            rr_str += " ⚠️ 偏低"
        elif rr > 3:
            rr_str += " ✅ 优秀"
        lines.append(f"  盈亏比：{rr_str}")
        lines.append("")

    # 大盘背景
    if market_context:
        lines.append("🌍 大盘背景")
        for c in market_context:
            lines.append(f"  · {c}")
        lines.append("")

    lines.append(f"_{stamp} · 系统判断，最终决策看图确认_")

    caption = "\n".join(lines)

    # ============= 出图 =============
    if chart_mod:
        # 优先形态突破图
        # 优先 4H 形态，其次 1H
        pattern_hits_all = [p for k, p in hits if k == "pattern"]
        pattern_hits_all.sort(key=lambda p: 0 if p.get("timeframe") == "4H" else 1)
        pattern_hit_for_chart = pattern_hits_all[0] if pattern_hits_all else None
        if pattern_hit_for_chart:
            try:
                p_tf = pattern_hit_for_chart.get("timeframe", "4H").lower()
                k4h = klines(sym, p_tf, 100)
                png = chart_mod.chart_pattern(sym, pattern_hit_for_chart["pattern_data"], k4h, timeframe=p_tf.upper())
                push_all(caption, png)
                try: os.remove(png)
                except: pass
                return
            except Exception as e:
                print(f"[chart_pattern:{sym}] {e}")

        # 次优先：通道图
        if meta.get("channel_ch"):
            try:
                tf = meta.get("channel_tf", "2H")
                k_bars = klines(sym, tf.lower(), 80)
                png = chart_mod.chart_channel(sym, meta["channel_ch"], k_bars, timeframe=tf)
                push_all(caption, png)
                try: os.remove(png)
                except: pass
                return
            except Exception as e:
                print(f"[chart_channel:{sym}] {e}")

        # 有 EMA 信号 → 画 EMA 图
        has_ema_hit = any(kind == "ema" for kind, _ in hits)
        if has_ema_hit:
            try:
                # 判断 EMA 事件是发生在 4H 还是 1D
                is_1d_signal = any("1D" in payload.get("text", "") for kind, payload in hits if kind == "ema")
                interval = "1d" if is_1d_signal else "4h"
                k_data = klines(sym, interval, 150)
                closes_x = [float(k[4]) for k in k_data]
                ema_short = ema_series(closes_x, 21) if len(closes_x) >= 21 else []
                ema_long = ema_series(closes_x, 100) if len(closes_x) >= 100 else []
                if ema_short and ema_long:
                    # 找出信号里最重要的 EMA 名字
                    ema_name = "EMA21"
                    ema_val = ema_short[-2] if len(ema_short) >= 2 else 0
                    for kind, payload in hits:
                        if kind == "ema":
                            for cand in ["EMA200", "EMA100", "EMA52", "EMA21", "EMA12"]:
                                if cand in payload.get("text", ""):
                                    ema_name = cand
                                    period = int(cand.replace("EMA", ""))
                                    if len(closes_x) >= period + 3:
                                        ema_val = ema_series(closes_x, period)[-2]
                                    break
                    close_price = closes_x[-2]
                    png = chart_mod.chart_ema_touch(sym, k_data, ema_short, ema_long, close_price, ema_name, ema_val)
                    push_all(caption, png)
                    try: os.remove(png)
                    except: pass
                    return
            except Exception as e:
                print(f"[chart_ema:{sym}] {e}")

    # 兜底：纯文本
    push_all(caption)


if __name__ == "__main__":
    main()
