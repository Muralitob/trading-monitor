"""
山寨币涨幅榜扫描器 · 每 5 分钟
只监控山寨币，找当下最火的
- 过滤：BTC/ETH/SOL/BNB 等大盘、稳定币、美股 perp
- 排序：24h 涨跌幅
- 分别推：涨幅榜 top 10 + 跌幅榜 top 10
"""
import sys, os, json, urllib.request, datetime, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    _load_state, _save_state, already_fired, mark_fired, today_key,
    CHAT_ID,
)

# 山寨榜专用 bot（跟主 bot 分开）
ALT_TG_TOKEN = os.environ.get("ALT_TG_TOKEN", "").strip()


def send_alt_tg(text):
    """用山寨榜专属 bot 推送（如未配置则回退主 bot）"""
    token = ALT_TG_TOKEN if ALT_TG_TOKEN else os.environ.get("TG_TOKEN", "")
    if not token:
        print("⚠️ ALT_TG_TOKEN 和 TG_TOKEN 都没设")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()

BASE = "https://fapi.binance.com"

# 排除的品种
EXCLUDE_MAJORS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                  "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT",
                  "MATICUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT"}

# 排除的美股 perp（避免混入）
EXCLUDE_STOCK_KEYWORDS = ["USDT"]  # 会二次过滤
STOCK_TICKERS = {"NVDA", "TSLA", "META", "AAPL", "GOOGL", "AMZN", "MSFT",
                 "MSTR", "COIN", "HOOD", "SMCI", "MU", "SNDK", "SKHYNIX",
                 "TSM", "QCOM", "AMD", "INTC", "BABA", "PYPL", "NFLX",
                 "IBM", "CRM", "JPM", "SPY", "QQQ", "IWM", "SOXL", "SOXS",
                 "KORU", "TSLL", "TSLZ", "NVDX", "NVDL", "EWY", "RKLB",
                 "CRCL", "PLTR", "UBER", "SOFI", "ROKU", "SNAP", "COIN",
                 "XAU", "XAG", "COPPER", "GOLD", "SILVER", "DRAM", "BANK",
                 "TQQQ", "SQQQ", "UPRO", "SPXL", "SPXS", "CLUSDT", "INXUSDT"}

TOP_N = 10          # 各 top 数
MIN_VOL_MM = 20     # 最低 24h 成交量（百万 USDT）


def fetch(url):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(r, timeout=10).read())
    except Exception:
        return None


def is_altcoin(sym):
    """判断是否山寨币（排除大盘和美股）"""
    if sym in EXCLUDE_MAJORS:
        return False
    if not sym.endswith("USDT"):
        return False
    base = sym[:-4]  # 去掉 USDT
    if base in STOCK_TICKERS:
        return False
    # 太长的（超过 12 字符）通常是杠杆代币或奇怪玩意
    if len(base) > 12:
        return False
    return True


def fmt_price(p):
    if p >= 1000: return f"${p:,.1f}"
    if p >= 10: return f"${p:.3f}"
    if p >= 1: return f"${p:.4f}"
    return f"${p:.6f}"


def fmt_vol(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    return f"${v/1e3:.0f}K"


def main():
    data = fetch(f"{BASE}/fapi/v1/ticker/24hr")
    if not data:
        print("❌ 拉不到数据")
        return

    # 过滤 + 排序
    alts = []
    for t in data:
        sym = t["symbol"]
        if not is_altcoin(sym): continue
        vol_mm = float(t.get("quoteVolume", 0)) / 1e6
        if vol_mm < MIN_VOL_MM: continue
        alts.append({
            "symbol": sym,
            "short": sym[:-4],
            "price": float(t["lastPrice"]),
            "change_24h": float(t["priceChangePercent"]),
            "vol_mm": vol_mm,
            "high_24h": float(t["highPrice"]),
            "low_24h": float(t["lowPrice"]),
        })

    if not alts:
        print("无山寨币数据")
        return

    # 涨幅榜
    gainers = sorted(alts, key=lambda x: -x["change_24h"])[:TOP_N]
    # 跌幅榜
    losers = sorted(alts, key=lambda x: x["change_24h"])[:TOP_N]

    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"🔥 *山寨币榜 · {now.strftime('%m-%d %H:%M')} BJ*"]
    lines.append(f"`━━━━━━━━━━━━━━━━━━━`")
    lines.append("")

    # 涨幅榜
    lines.append("🟢 *涨幅榜 TOP 10*")
    lines.append("")
    for i, a in enumerate(gainers, 1):
        # 涨幅位置：现价在 24h 区间的哪
        range_ = a["high_24h"] - a["low_24h"]
        if range_ > 0:
            pos = (a["price"] - a["low_24h"]) / range_ * 100
            pos_note = "🔝" if pos > 90 else ("⬆" if pos > 60 else "")
        else:
            pos_note = ""
        lines.append(f"`{i:>2}` `{a['short']:<10}` +{a['change_24h']:>5.2f}%  {fmt_price(a['price'])}  Vol {fmt_vol(a['vol_mm']*1e6)} {pos_note}")
    lines.append("")

    # 跌幅榜
    lines.append("🔴 *跌幅榜 TOP 10*")
    lines.append("")
    for i, a in enumerate(losers, 1):
        range_ = a["high_24h"] - a["low_24h"]
        if range_ > 0:
            pos = (a["price"] - a["low_24h"]) / range_ * 100
            pos_note = "🔽" if pos < 10 else ("⬇" if pos < 40 else "")
        else:
            pos_note = ""
        lines.append(f"`{i:>2}` `{a['short']:<10}` {a['change_24h']:>+.2f}%  {fmt_price(a['price'])}  Vol {fmt_vol(a['vol_mm']*1e6)} {pos_note}")
    lines.append("")

    lines.append(f"_{now.strftime('%H:%M')} · 山寨榜（已排除 BTC/ETH/主流 + 美股 perp）_")

    text = "\n".join(lines)
    print(text[:400])
    print("...")

    # 去重：15 分钟一次（榜单变化没那么快，避免刷屏）
    state = _load_state()
    dk = today_key()
    slot = now.hour * 4 + now.minute // 15
    key = f"alt_rank:{dk}:{slot}"
    if already_fired(state, key):
        print("15min 内已推过，跳过")
        return
    mark_fired(state, key)
    _save_state(state)

    try:
        send_alt_tg(text)
        print("✓ 推送成功（山寨榜专属 bot）")
    except Exception as e:
        print(f"推送失败: {e}")


if __name__ == "__main__":
    main()
