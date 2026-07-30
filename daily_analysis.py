"""
每日行情分析 · 每天早上 8:00 (BJ)
输出格式：飞书交互卡片（分区、颜色、加粗）
"""
import sys, os, datetime, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    klines, ema_series, detect_channel,
    ALL_SYMBOLS, VOLATILITY_SYMBOLS, RULES,
    FEISHU_WEBHOOK,
)


def analyze_symbol(sym):
    try:
        k4h = klines(sym, "4h", 150)
        k1d = klines(sym, "1d", 60)
        closes_4h = [float(k[4]) for k in k4h]
        closes_1d = [float(k[4]) for k in k1d]
        ema25 = ema_series(closes_4h, 25)[-1] if len(closes_4h) >= 25 else None
        ema100 = ema_series(closes_4h, 100)[-1] if len(closes_4h) >= 100 else None
        ema50d = ema_series(closes_1d, 50)[-1] if len(closes_1d) >= 50 else None
        cur = closes_4h[-1]

        change_24h = (cur - closes_4h[-7]) / closes_4h[-7] * 100 if len(closes_4h) >= 7 else 0

        k2h = klines(sym, "2h", 200)
        ch = detect_channel(k2h)
        channel_info = None
        if ch:
            width = ch["upper"] - ch["lower"]
            pos = (cur - ch["lower"]) / width * 100
            channel_info = {
                "dir": ch["direction"],
                "upper": ch["upper"],
                "lower": ch["lower"],
                "pos_pct": pos,
                "touches": f"{ch['touch_upper']}/{ch['touch_lower']}",
            }

        return {
            "symbol": sym,
            "short": sym.replace("USDT", ""),
            "price": cur,
            "change_24h": change_24h,
            "ema25_dist": (cur - ema25) / ema25 * 100 if ema25 else None,
            "ema100_dist": (cur - ema100) / ema100 * 100 if ema100 else None,
            "ema50d_dist": (closes_1d[-1] - ema50d) / ema50d * 100 if ema50d else None,
            "ema25": ema25,
            "ema100": ema100,
            "channel": channel_info,
        }
    except Exception as e:
        print(f"[{sym}] error: {e}")
        return None


def fmt_price(p):
    """智能格式化价格"""
    if p >= 10000:
        return f"${p:,.0f}"
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.3f}"
    return f"${p:.5f}"


def color_change(pct):
    """按涨跌返回带颜色的文字"""
    if pct >= 0.5:
        return f"<font color='green'>+{pct:.2f}%</font>"
    if pct <= -0.5:
        return f"<font color='red'>{pct:.2f}%</font>"
    return f"<font color='grey'>{pct:+.2f}%</font>"


def color_trend(pct):
    """趋势距离染色"""
    if pct >= 5:
        return f"<font color='green'>**{pct:+.1f}%**</font>"
    if pct >= 0:
        return f"<font color='green'>{pct:+.1f}%</font>"
    if pct >= -5:
        return f"<font color='red'>{pct:+.1f}%</font>"
    return f"<font color='red'>**{pct:+.1f}%**</font>"


def build_card(data):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    date_str = now.strftime("%m-%d %H:%M")

    elements = []

    # ========== 1. 大盘概览 ==========
    lines = ["**🌏 大盘概览**"]
    for sym in ["BTCUSDT", "ETHUSDT", "XAUUSDT"]:
        d = data.get(sym)
        if d:
            arrow = "📈" if d["change_24h"] >= 0 else "📉"
            lines.append(f"{arrow} `{d['short']:<5}` {fmt_price(d['price']):>10}   {color_change(d['change_24h'])}")
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    elements.append({"tag": "hr"})

    # ========== 2. 趋势强弱排名 ==========
    ranked = sorted(
        [d for d in data.values() if d and d.get("ema50d_dist") is not None],
        key=lambda x: -x["ema50d_dist"]
    )
    lines = ["**📊 趋势强弱**（vs 日线EMA50）"]
    for d in ranked:
        icon = "🟢" if d["ema50d_dist"] > 0 else "🔴"
        lines.append(f"{icon} `{d['short']:<8}` {color_trend(d['ema50d_dist'])}")
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    elements.append({"tag": "hr"})

    # ========== 3. 距关键均线近的品种 ==========
    close_signals = []
    for d in data.values():
        if not d:
            continue
        if d.get("ema100_dist") is not None and abs(d["ema100_dist"]) < 2:
            close_signals.append((d, "EMA100", d["ema100_dist"], d["ema100"], "🌟"))
        if d.get("ema25_dist") is not None and abs(d["ema25_dist"]) < 1.5:
            close_signals.append((d, "EMA25", d["ema25_dist"], d["ema25"], "📊"))

    if close_signals:
        lines = ["**⚡ 今日重点 · 近关键均线**"]
        for d, ema_name, dist, val, icon in close_signals:
            dist_c = f"<font color='green'>{dist:+.2f}%</font>" if abs(dist) < 0.5 else f"{dist:+.2f}%"
            lines.append(f"{icon} `{d['short']:<8}` 距 {ema_name} {dist_c} ({fmt_price(val)})")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        elements.append({"tag": "hr"})

    # ========== 4. 活跃通道 ==========
    channels = [d for d in data.values() if d and d.get("channel")]
    if channels:
        lines = ["**📐 活跃通道**"]
        # 按位置排序，接近上下沿的优先
        channels_sorted = sorted(channels, key=lambda d: abs(d["channel"]["pos_pct"] - 50), reverse=True)
        for d in channels_sorted:
            ch = d["channel"]
            pos = ch["pos_pct"]
            if pos < 20:
                pos_desc = "🔻 <font color='red'>**接近下沿**</font>"
            elif pos > 80:
                pos_desc = "🔺 <font color='green'>**接近上沿**</font>"
            elif pos < 40:
                pos_desc = "下半区"
            elif pos > 60:
                pos_desc = "上半区"
            else:
                pos_desc = "中部"

            dir_short = "跌" if ch["dir"] == "下降通道" else ("涨" if ch["dir"] == "上升通道" else "平")
            lines.append(f"`{d['short']:<8}` {dir_short}通道 · 位置 {pos:.0f}%  {pos_desc}")
            lines.append(f"   ↕ 上 {fmt_price(ch['upper'])} / 下 {fmt_price(ch['lower'])}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})
        elements.append({"tag": "hr"})

    # ========== 5. 操作建议 ==========
    lines = ["**🎯 操作建议**"]
    suggestions_found = False
    for d in ranked:
        sym_sug = []

        # EMA 触碰机会
        if d.get("ema100_dist") is not None and abs(d["ema100_dist"]) < 1.2:
            trend_side = "支撑" if d["ema100_dist"] > 0 else "阻力"
            sym_sug.append(f"🌟 距 EMA100 {d['ema100_dist']:+.2f}%，可能触发**大级别{trend_side}测试**")

        if d.get("ema25_dist") is not None and abs(d["ema25_dist"]) < 0.6:
            sym_sug.append(f"📊 距 EMA25 {d['ema25_dist']:+.2f}%，动态均线触碰在即")

        # 通道机会
        if d.get("channel"):
            ch = d["channel"]
            if ch["pos_pct"] < 15:
                if ch["dir"] == "下降通道":
                    sym_sug.append(f"📐 通道下沿 · <font color='orange'>逆势做多机会（谨慎）</font>")
                else:
                    sym_sug.append(f"📐 通道下沿 · <font color='green'>顺势做多</font>")
            elif ch["pos_pct"] > 85:
                if ch["dir"] == "下降通道":
                    sym_sug.append(f"📐 通道上沿 · <font color='red'>顺势做空</font>")
                else:
                    sym_sug.append(f"📐 通道上沿 · <font color='orange'>逆势做空（谨慎）</font>")

        if sym_sug:
            trend_tag = ""
            if d.get("ema50d_dist") is not None:
                if d["ema50d_dist"] > 5:
                    trend_tag = " <font color='green'>[强多]</font>"
                elif d["ema50d_dist"] < -5:
                    trend_tag = " <font color='red'>[强空]</font>"
                elif abs(d["ema50d_dist"]) < 2:
                    trend_tag = " <font color='grey'>[震荡]</font>"

            lines.append(f"\n**`{d['short']}`**{trend_tag}")
            for s in sym_sug:
                lines.append(f"　• {s}")
            suggestions_found = True

    if not suggestions_found:
        lines.append("\n<font color='grey'>今日无明显机会，建议观望</font>")

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    # 页脚
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": "数据来自 Binance 合约 · 仅供参考，不构成投资建议"
        }]
    })

    # 组装卡片
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"📊 每日行情分析 · {date_str}"
            },
            "template": "blue"
        },
        "elements": elements,
    }
    return card


def send_feishu_card(card):
    if not FEISHU_WEBHOOK:
        print("⚠️ FEISHU_WEBHOOK 未设置")
        return
    url = FEISHU_WEBHOOK if FEISHU_WEBHOOK.startswith("http") else \
          f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_WEBHOOK}"
    payload = {"msg_type": "interactive", "card": card}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=10).read().decode()
    print(f"飞书响应: {resp}")


def main():
    print("拉取所有品种数据中...")
    data = {}
    for sym in ALL_SYMBOLS:
        data[sym] = analyze_symbol(sym)

    card = build_card(data)
    send_feishu_card(card)
    print("✓ 已推送到飞书")


if __name__ == "__main__":
    main()
