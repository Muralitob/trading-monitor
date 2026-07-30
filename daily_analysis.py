"""
每日行情分析 · 每天早上 8:00 (BJ) 生成

生成内容：
1. 大盘概览（BTC/ETH/XAU + 24h 变化）
2. 每个品种的：现价、EMA25/100 距离、日EMA50 距离、24h 变化
3. 趋势排名（按 vs 日EMA50 排序）
4. 距关键位近的品种（可能今日触发信号）
5. 操作建议（结合 briefing 打分制）
"""
import sys, os, datetime, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    klines, ema_series, detect_channel,
    ALL_SYMBOLS, VOLATILITY_SYMBOLS, RULES,
    push_all, send_feishu, FEISHU_WEBHOOK,
    TOKEN, CHAT_ID,
)


def analyze_symbol(sym):
    """返回一个 dict 描述该品种当前状态"""
    try:
        k4h = klines(sym, "4h", 150)
        k1d = klines(sym, "1d", 60)
        closes_4h = [float(k[4]) for k in k4h]
        closes_1d = [float(k[4]) for k in k1d]
        ema25 = ema_series(closes_4h, 25)[-1] if len(closes_4h) >= 25 else None
        ema100 = ema_series(closes_4h, 100)[-1] if len(closes_4h) >= 100 else None
        ema50d = ema_series(closes_1d, 50)[-1] if len(closes_1d) >= 50 else None
        cur = closes_4h[-1]

        # 24h 变化（从 24h 前的收盘价起算）
        if len(closes_4h) >= 7:
            change_24h = (cur - closes_4h[-7]) / closes_4h[-7] * 100
        else:
            change_24h = 0

        # 通道检测
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


def compose_report(data):
    """把所有品种数据组装成人类可读的报告"""
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)  # BJ 时间
    lines = []
    lines.append(f"📊 每日行情分析 · {now.strftime('%m-%d %H:%M')} BJ")
    lines.append("")

    # 1. 大盘概览
    lines.append("【大盘概览】")
    for sym in ["BTCUSDT", "ETHUSDT", "XAUUSDT"]:
        d = data.get(sym)
        if d:
            arrow = "📈" if d["change_24h"] >= 0 else "📉"
            lines.append(f"{arrow} {sym[:-4]:<4} ${d['price']:.2f}  {d['change_24h']:+.2f}% (24h)")
    lines.append("")

    # 2. 趋势排名（按 vs 日EMA50 排序）
    ranked = sorted(
        [d for d in data.values() if d and d.get("ema50d_dist") is not None],
        key=lambda x: -x["ema50d_dist"]
    )
    lines.append("【趋势强弱排名】(vs 日EMA50)")
    for d in ranked:
        icon = "🟢" if d["ema50d_dist"] > 0 else "🔴"
        lines.append(f"{icon} {d['symbol'][:-4]:<8} {d['ema50d_dist']:+.1f}%")
    lines.append("")

    # 3. 近距离信号（EMA25 / EMA100 附近）
    close_signals = []
    for d in data.values():
        if not d:
            continue
        if d.get("ema25_dist") is not None and abs(d["ema25_dist"]) < 1.5:
            close_signals.append((d["symbol"], "EMA25", d["ema25_dist"], d["ema25"]))
        if d.get("ema100_dist") is not None and abs(d["ema100_dist"]) < 2:
            close_signals.append((d["symbol"], "EMA100", d["ema100_dist"], d["ema100"]))
    if close_signals:
        lines.append("【今日重点关注 · 近关键均线】")
        for sym, ema_name, dist, val in close_signals:
            lines.append(f"⚡ {sym[:-4]:<8} 距 {ema_name} {dist:+.2f}% (${val:.2f})")
        lines.append("")

    # 4. 通道信息
    channels = [d for d in data.values() if d and d.get("channel")]
    if channels:
        lines.append("【活跃通道】")
        for d in channels:
            ch = d["channel"]
            pos_desc = "接近下沿⬇️" if ch["pos_pct"] < 25 else \
                       "接近上沿⬆️" if ch["pos_pct"] > 75 else "通道中部"
            lines.append(f"📐 {d['symbol'][:-4]:<8} {ch['dir']}  位置 {ch['pos_pct']:.0f}% {pos_desc}")
            lines.append(f"    上沿 ${ch['upper']:.2f} / 下沿 ${ch['lower']:.2f}")
        lines.append("")

    # 5. 操作建议
    lines.append("【操作建议 · 按打分制评估】")
    # 找有价值的机会
    for d in ranked:
        sym = d["symbol"]
        suggestions = []

        # EMA 触碰
        if d.get("ema25_dist") is not None and abs(d["ema25_dist"]) < 0.8:
            suggestions.append(f"距 EMA25 仅 {d['ema25_dist']:+.2f}% → 关注 4H 触碰")

        if d.get("ema100_dist") is not None and abs(d["ema100_dist"]) < 1.2:
            suggestions.append(f"🌟 距 EMA100 仅 {d['ema100_dist']:+.2f}% → 大级别支撑测试")

        # 通道
        if d.get("channel"):
            ch = d["channel"]
            if ch["pos_pct"] < 15:
                dir_ = "反弹做多（逆势谨慎）" if ch["dir"] == "下降通道" else "顺势做多"
                suggestions.append(f"通道下沿 → {dir_}")
            elif ch["pos_pct"] > 85:
                dir_ = "顺势做空" if ch["dir"] == "下降通道" else "回落做空（逆势谨慎）"
                suggestions.append(f"通道上沿 → {dir_}")

        # 趋势对齐提醒
        trend_note = ""
        if d.get("ema50d_dist") is not None:
            if d["ema50d_dist"] > 5:
                trend_note = " [强多]"
            elif d["ema50d_dist"] < -5:
                trend_note = " [强空]"
            elif abs(d["ema50d_dist"]) < 2:
                trend_note = " [震荡]"

        if suggestions:
            lines.append(f"• {sym[:-4]}{trend_note}")
            for s in suggestions:
                lines.append(f"    {s}")

    if not any("•" in l for l in lines[-30:]):
        lines.append("（今日无明显机会，观望即可）")

    lines.append("")
    lines.append("_数据来自 Binance 合约。仅供参考，不构成投资建议。_")
    return "\n".join(lines)


def main():
    print("拉取所有品种数据中...")
    data = {}
    for sym in ALL_SYMBOLS:
        data[sym] = analyze_symbol(sym)

    report = compose_report(data)
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60 + "\n")

    # 只推飞书（每日汇总不适合发到 Telegram 因为已经每分钟有信号）
    if FEISHU_WEBHOOK:
        send_feishu(report, title=None)
        print("✓ 已推送到飞书")
    else:
        print("⚠️ 未配置 FEISHU_WEBHOOK，跳过飞书推送")


if __name__ == "__main__":
    main()
