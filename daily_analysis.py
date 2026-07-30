"""
每日行情预案 · 每天早上 8:00 (BJ)
每个品种输出具体入场区间/止损/状态
"""
import sys, os, datetime, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    klines, ema_series, detect_channel,
    ALL_SYMBOLS, FEISHU_WEBHOOK,
)


def analyze_symbol(sym):
    try:
        k4h = klines(sym, "4h", 150)
        k1d = klines(sym, "1d", 60)
        k2h = klines(sym, "2h", 200)
        closes_4h = [float(k[4]) for k in k4h]
        closes_1d = [float(k[4]) for k in k1d]
        highs_4h = [float(k[2]) for k in k4h]
        lows_4h = [float(k[3]) for k in k4h]

        ema25 = ema_series(closes_4h, 25)[-1] if len(closes_4h) >= 25 else None
        ema100 = ema_series(closes_4h, 100)[-1] if len(closes_4h) >= 100 else None
        ema50d = ema_series(closes_1d, 50)[-1] if len(closes_1d) >= 50 else None
        cur = closes_4h[-1]

        # 24h 变化
        change_24h = (cur - closes_4h[-7]) / closes_4h[-7] * 100 if len(closes_4h) >= 7 else 0

        # 近 30 根 4H 的极值（用作止损参考）
        recent_high = max(highs_4h[-30:])
        recent_low = min(lows_4h[-30:])

        ch = detect_channel(k2h)

        return {
            "symbol": sym,
            "short": sym.replace("USDT", ""),
            "price": cur,
            "change_24h": change_24h,
            "ema25": ema25, "ema100": ema100, "ema50d": ema50d,
            "ema50d_dist": (closes_1d[-1] - ema50d) / ema50d * 100 if ema50d else 0,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "channel": ch,
        }
    except Exception as e:
        print(f"[{sym}] error: {e}")
        return None


def fmt(p):
    if p is None: return "-"
    if p >= 10000: return f"${p:,.0f}"
    if p >= 100: return f"${p:,.2f}"
    if p >= 1: return f"${p:.3f}"
    return f"${p:.5f}"


def build_plan(d):
    """
    根据数据生成交易预案：
    - direction: SHORT / LONG / WAIT
    - entry_low/high: 入场区间
    - sl: 止损价
    - tp1: 第一目标
    - rr: 盈亏比
    - status: 启用 / 观察 / 停用 / 已触发
    - reason: 简述
    """
    cur = d["price"]
    ema25 = d["ema25"]
    ema100 = d["ema100"]
    ch = d["channel"]
    trend = d["ema50d_dist"]

    # 主趋势判定
    if trend > 3:
        bias = "UP"
    elif trend < -3:
        bias = "DOWN"
    else:
        bias = "FLAT"

    # 收集当前价上方的"阻力候选"和下方的"支撑候选"
    resistances = []
    supports = []
    for name, val in [("EMA25", ema25), ("EMA100", ema100)]:
        if val is None: continue
        if val > cur * 1.001:
            resistances.append((name, val))
        elif val < cur * 0.999:
            supports.append((name, val))
    if ch:
        if ch["upper"] > cur:
            resistances.append(("通道上沿", ch["upper"]))
        if ch["lower"] < cur:
            supports.append(("通道下沿", ch["lower"]))

    # 按距离排序
    resistances.sort(key=lambda x: x[1])
    supports.sort(key=lambda x: -x[1])

    # 生成预案
    direction = "WAIT"
    entry_low = entry_high = sl = tp1 = None
    rr = 0
    reason = ""

    if bias == "DOWN" and resistances:
        # 顺势做空：找最近上方阻力做反弹空
        r_name, r_val = resistances[0]
        entry_low = r_val * 0.995
        entry_high = r_val * 1.003
        # 止损用第二阻力或最近30根4H高点
        if len(resistances) > 1:
            sl = resistances[1][1] * 1.005
        else:
            sl = max(r_val * 1.02, d["recent_high"] * 1.003)
        # TP1 = 下方最近支撑
        if supports:
            tp1 = supports[0][1]
        else:
            tp1 = cur * 0.95
        direction = "SHORT"
        reason = f"反弹到 {r_name} 空"

    elif bias == "UP" and supports:
        s_name, s_val = supports[0]
        entry_low = s_val * 0.997
        entry_high = s_val * 1.005
        if len(supports) > 1:
            sl = supports[1][1] * 0.995
        else:
            sl = min(s_val * 0.98, d["recent_low"] * 0.997)
        if resistances:
            tp1 = resistances[0][1]
        else:
            tp1 = cur * 1.05
        direction = "LONG"
        reason = f"回踩 {s_name} 多"

    elif bias == "FLAT":
        # 震荡区间，选距离较近的一侧机会
        if ch:
            width = ch["upper"] - ch["lower"]
            pos = (cur - ch["lower"]) / width
            if pos < 0.35 and supports:
                # 接近下沿做多
                entry_low = ch["lower"] * 0.998
                entry_high = ch["lower"] * 1.008
                sl = ch["lower"] * 0.985
                tp1 = ch["upper"] * 0.995
                direction = "LONG"
                reason = "震荡下沿多"
            elif pos > 0.65 and resistances:
                entry_low = ch["upper"] * 0.992
                entry_high = ch["upper"] * 1.002
                sl = ch["upper"] * 1.015
                tp1 = ch["lower"] * 1.005
                direction = "SHORT"
                reason = "震荡上沿空"

    # 计算 R:R
    if direction != "WAIT":
        entry_mid = (entry_low + entry_high) / 2
        if direction == "SHORT":
            risk = sl - entry_mid
            reward = entry_mid - tp1
        else:
            risk = entry_mid - sl
            reward = tp1 - entry_mid
        rr = reward / risk if risk > 0 else 0

    # 状态判定
    if direction == "WAIT":
        status = "停用"
        status_reason = "无明确方向"
    elif rr < 1.5:
        status = "停用"
        status_reason = f"R:R {rr:.2f}<1.5"
    elif entry_low <= cur <= entry_high:
        status = "已触发"
        status_reason = "现价在入场区，等确认"
    elif rr < 2:
        status = "观察"
        status_reason = f"R:R {rr:.2f}"
    else:
        status = "启用"
        status_reason = ""

    return {
        "direction": direction,
        "entry_low": entry_low, "entry_high": entry_high,
        "sl": sl, "tp1": tp1, "rr": rr,
        "status": status, "reason": reason, "status_reason": status_reason,
    }


def render_plan_text(d, plan):
    """把预案渲染成可读的一行字（飞书 lark_md）"""
    p = plan
    if p["direction"] == "WAIT":
        return "<font color='grey'>无预案（等待方向明确）</font>"

    dir_zh = "反弹空" if p["direction"] == "SHORT" else "回踩多"
    entry_range = f"{fmt(p['entry_low'])} – {fmt(p['entry_high'])}"
    sl_text = f"SL {fmt(p['sl'])}"
    rr_text = f"R:R {p['rr']:.2f}"
    return f"{entry_range} **{dir_zh}** · {sl_text} · <font color='grey'>{rr_text}</font>"


def status_badge(status):
    color = {
        "启用": "green",
        "观察": "orange",
        "已触发": "purple",
        "停用": "grey",
    }.get(status, "grey")
    return f"<font color='{color}'>**{status}**</font>"


def build_card(symbols_data):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    date_str = now.strftime("%m-%d %H:%M")

    elements = []

    # 头部说明
    enabled_count = sum(1 for d, p in symbols_data if p["status"] == "启用")
    triggered_count = sum(1 for d, p in symbols_data if p["status"] == "已触发")
    header_text = (
        f"已完成 **{len(symbols_data)}** 个标的的实时分析。"
        f"当前 <font color='green'>**{enabled_count}** 个启用</font>、"
        f"<font color='purple'>**{triggered_count}** 个已触发等待确认</font>。\n"
        f"自动交易保持完全关闭，未执行任何订单。\n\n"
        f"统一行情截取时间：**{now.strftime('%Y-%m-%d %H:%M')}** (UTC+8)  ·  Binance 永续"
    )
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": header_text}})
    elements.append({"tag": "hr"})

    # 表头
    elements.append({
        "tag": "div",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": "**标的**"}},
            {"is_short": True, "text": {"tag": "lark_md", "content": "**当前价 · 24h**"}},
            {"is_short": False, "text": {"tag": "lark_md", "content": "**今日预案** · 状态"}},
        ],
    })
    elements.append({"tag": "hr"})

    # 按启用优先级排序：启用 > 已触发 > 观察 > 停用
    order = {"启用": 0, "已触发": 1, "观察": 2, "停用": 3}
    symbols_data.sort(key=lambda x: (order.get(x[1]["status"], 99), x[0]["short"]))

    for d, p in symbols_data:
        # 24h 涨跌染色
        chg = d["change_24h"]
        chg_c = f"<font color='green'>+{chg:.2f}%</font>" if chg >= 0 else f"<font color='red'>{chg:.2f}%</font>"
        # 趋势标签
        if d["ema50d_dist"] > 3:
            tag = "<font color='green'>强多</font>"
        elif d["ema50d_dist"] < -3:
            tag = "<font color='red'>强空</font>"
        else:
            tag = "<font color='grey'>震荡</font>"

        left = f"**`{d['short']}`**\n{tag}"
        mid = f"**{fmt(d['price'])}**\n{chg_c}"
        plan_text = render_plan_text(d, p)
        status_text = f"{status_badge(p['status'])}"
        if p["status_reason"]:
            status_text += f"  <font color='grey'>· {p['status_reason']}</font>"
        right = f"{plan_text}\n{status_text}"

        elements.append({
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": left}},
                {"is_short": True, "text": {"tag": "lark_md", "content": mid}},
                {"is_short": False, "text": {"tag": "lark_md", "content": right}},
            ],
        })

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": "数据来自 Binance 永续合约 · 预案由算法自动生成，仅供参考不构成投资建议"
        }]
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 每日交易预案 · {date_str}"},
            "template": "blue",
        },
        "elements": elements,
    }


def send_feishu_card(card):
    if not FEISHU_WEBHOOK:
        print("⚠️ FEISHU_WEBHOOK 未设置")
        return
    url = FEISHU_WEBHOOK if FEISHU_WEBHOOK.startswith("http") else \
          f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_WEBHOOK}"
    payload = {"msg_type": "interactive", "card": card}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10).read().decode()
    print(f"飞书响应: {resp}")


def main():
    print("拉数据 + 生成预案中...")
    symbols_data = []
    for sym in ALL_SYMBOLS:
        d = analyze_symbol(sym)
        if not d:
            continue
        p = build_plan(d)
        symbols_data.append((d, p))

    card = build_card(symbols_data)
    send_feishu_card(card)
    print("✓ 已推送到飞书")


if __name__ == "__main__":
    main()
