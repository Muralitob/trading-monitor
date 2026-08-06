"""
早间行情快报 · 每天早上 7:30 BJ 推 Telegram
覆盖：美股板块、重点标的、加密大盘
"""
import sys, os, json, urllib.request, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    klines, ema_series, send_tg,
    TOKEN, CHAT_ID,
)

BASE = "https://fapi.binance.com"


# ========== 分组配置 ==========
SECTORS = {
    "加密概念":  ["COINUSDT", "MSTRUSDT", "HOODUSDT", "CRCLUSDT"],
    "Mag7":     ["NVDAUSDT", "TSLAUSDT", "METAUSDT", "AAPLUSDT", "GOOGLUSDT", "AMZNUSDT", "MSFTUSDT"],
    "指数&杠杆": ["QQQUSDT", "SPYUSDT", "IWMUSDT", "SOXLUSDT", "KORUUSDT"],
    "商品&金融": ["XAUUSDT", "XAGUSDT", "COPPERUSDT", "JPMUSDT"],
    "半导体":    ["SMCIUSDT", "MUUSDT", "SNDKUSDT", "NVDAUSDT", "SOXLUSDT", "SKHYNIXUSDT", "TSMUSDT"],
}

# 重点标的（在快报里逐一列出）
FOCUS_SYMBOLS = [
    "SMCIUSDT", "MUUSDT", "SOXLUSDT", "SNDKUSDT",
    "NVDAUSDT", "TSLAUSDT", "METAUSDT",
    "COINUSDT", "MSTRUSDT",
    "XAUUSDT", "XAGUSDT", "COPPERUSDT",
    "QQQUSDT", "SPYUSDT",
]

CRYPTO_MAIN = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "HYPEUSDT"]


def fetch(url):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(r, timeout=10).read())
    except Exception:
        return None


def get_all_tickers():
    data = fetch(f"{BASE}/fapi/v1/ticker/24hr")
    return {t["symbol"]: t for t in data} if data else {}


def get_funding_rate(sym):
    data = fetch(f"{BASE}/fapi/v1/premiumIndex?symbol={sym}")
    if data:
        return float(data.get("lastFundingRate", 0)) * 100
    return None


def get_oi(sym):
    data = fetch(f"{BASE}/fapi/v1/openInterest?symbol={sym}")
    if data:
        return float(data.get("openInterest", 0))
    return None


def emoji_dot(pct):
    return "🟢" if pct >= 0 else "🔴"


def fmt_price(p):
    if p >= 10000: return f"${p:,.2f}"
    if p >= 100: return f"${p:,.2f}"
    if p >= 1: return f"${p:.3f}"
    return f"${p:.5f}"


def fmt_vol(v):
    """成交量格式化"""
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    return f"${v/1e3:.0f}K"


def sector_stats(sector_symbols, tickers):
    """计算板块平均涨跌 + 范围"""
    changes = []
    for s in sector_symbols:
        t = tickers.get(s)
        if t:
            changes.append(float(t["priceChangePercent"]))
    if not changes:
        return None
    return {
        "avg": sum(changes) / len(changes),
        "min": min(changes),
        "max": max(changes),
        "n": len(changes),
    }


def build_brief():
    tickers = get_all_tickers()
    if not tickers:
        return "行情数据拉取失败"

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = []
    lines.append(f"☀️ *早间行情快报 · {now.strftime('%m-%d %H:%M')} BJ*")
    lines.append("`━━━━━━━━━━━━━━━━━━━━`")
    lines.append("")

    # ===== 一、美股板块 =====
    lines.append("🇺🇸 *【一、美股资金追逐热点】*")
    lines.append("")
    lines.append("*板块表现排名：*")
    sector_data = []
    for name, syms in SECTORS.items():
        st = sector_stats(syms, tickers)
        if st:
            sector_data.append((name, st))
    # 排序：平均涨幅从高到低
    sector_data.sort(key=lambda x: -x[1]["avg"])
    for name, st in sector_data:
        icon = emoji_dot(st["avg"])
        arrow = "↑" if st["avg"] >= 0 else "↓"
        lines.append(f"{icon} {arrow} *{name}*：平均 {st['avg']:+.2f}%  (范围 {st['min']:+.1f}% ~ {st['max']:+.1f}%)")
    lines.append("")

    # 重点标的
    lines.append("*重点标的：*")
    for sym in FOCUS_SYMBOLS:
        t = tickers.get(sym)
        if not t: continue
        short = sym.replace("USDT", "")
        chg = float(t["priceChangePercent"])
        price = float(t["lastPrice"])
        vol = float(t["quoteVolume"])
        icon = emoji_dot(chg)
        lines.append(f"`{short:<8}` {icon} {chg:+.2f}%  {fmt_price(price)}  Vol {fmt_vol(vol)}")
    lines.append("")

    # 资金流向判断
    if sector_data:
        top_sector = sector_data[0][0]
        bottom_sector = sector_data[-1][0]
        flow_msg = f"📌 *资金流向*：{top_sector} > {bottom_sector}"
        lines.append(flow_msg)
    lines.append("")
    lines.append("`━━━━━━━━━━━━━━━━━━━━`")
    lines.append("")

    # ===== 二、加密市场 =====
    lines.append("₿ *【二、加密市场】*")
    lines.append("")

    # BTC 详细
    btc = tickers.get("BTCUSDT")
    if btc:
        price = float(btc["lastPrice"])
        chg = float(btc["priceChangePercent"])
        high = float(btc["highPrice"])
        low = float(btc["lowPrice"])
        vol = float(btc["quoteVolume"])
        icon = emoji_dot(chg)

        lines.append(f"*BTC*: {fmt_price(price)} {icon} {chg:+.2f}%")
        lines.append(f"  24h 区间：{fmt_price(low)} → {fmt_price(high)}  |  成交量 {fmt_vol(vol)}")

        # EMA21 + 资金费率 + OI
        try:
            k4h = klines("BTCUSDT", "4h", 30)
            closes = [float(k[4]) for k in k4h]
            ema21 = ema_series(closes, 21)[-1]
            vs_ema = (price - ema21) / ema21 * 100
            fr = get_funding_rate("BTCUSDT")
            oi_btc = get_oi("BTCUSDT")
            oi_usd = (oi_btc * price) if oi_btc else 0

            extra = f"  EMA21 {fmt_price(ema21)}  |  vs EMA21 {vs_ema:+.2f}%"
            if fr is not None:
                fr_icon = "🟢" if fr > 0 else "🔴"
                extra += f"  |  费率 {fr_icon}{fr:+.4f}%"
            lines.append(extra)
            if oi_btc:
                lines.append(f"  OI: {oi_btc:,.0f} BTC (${oi_usd/1e9:.2f}B)")
        except Exception as e:
            print(f"[BTC extras] {e}")
    lines.append("")

    # 主流币
    lines.append("*主流币表现：*")
    for sym in CRYPTO_MAIN[1:]:  # 跳过 BTC
        t = tickers.get(sym)
        if not t: continue
        short = sym.replace("USDT", "")
        price = float(t["lastPrice"])
        chg = float(t["priceChangePercent"])
        vol = float(t["quoteVolume"])
        icon = emoji_dot(chg)
        lines.append(f"`{short:<6}` {fmt_price(price)}  {icon} {chg:+.2f}%  |  Vol {fmt_vol(vol)}")
    lines.append("")

    # 加密 vs 美股风险偏好
    btc_chg = float(btc["priceChangePercent"]) if btc else 0
    spy = tickers.get("SPYUSDT")
    spy_chg = float(spy["priceChangePercent"]) if spy else 0
    if btc_chg > 1 and spy_chg > 1:
        risk_msg = "🟢 加密+美股同涨，风险偏好回升"
    elif btc_chg < -1 and spy_chg < -1:
        risk_msg = "🔴 加密+美股同跌，避险情绪升温"
    elif btc_chg * spy_chg < 0:
        risk_msg = "🟡 加密与美股分化，观察后续"
    else:
        risk_msg = "🟡 加密与美股均震荡，无明显趋势"
    lines.append(f"📌 *风险偏好*：{risk_msg}")
    lines.append("")

    # 尾巴
    lines.append(f"_{now.strftime('%Y-%m-%d %H:%M')} · 数据来自 Binance 永续_")
    return "\n".join(lines)


def main():
    text = build_brief()
    print(text[:500])
    print("...")
    send_tg(text)
    print("✓ 已推送到 Telegram")


if __name__ == "__main__":
    main()
