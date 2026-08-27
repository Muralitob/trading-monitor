"""
主力行为扫描器 · 每 5 分钟
识别两种主力动作，推 @muragupiao_bot 之类的山寨 bot：

【顶部派发 → SHORT】
  价格横盘 + OI ↑ + 多空比 ↑（散户拼命做多）+ 主动卖出占比高
  = 主力压价把货派给散户

【底部吸筹 → LONG】
  价格横盘 + OI ↑ + 多空比 ↓（散户拼命做空）+ 主动买入占比高
  = 主力压价扫散户空单
"""
import sys, os, json, urllib.request, datetime, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    _load_state, _save_state, already_fired, mark_fired, today_key,
    CHAT_ID,
)

BASE = "https://fapi.binance.com"
ALT_TG_TOKEN = os.environ.get("ALT_TG_TOKEN", "").strip()

# 排除
EXCLUDE_MAJORS = set()  # 主力信号里 BTC/ETH 也有价值，全都扫
STOCK_TICKERS = {"NVDA", "TSLA", "META", "AAPL", "GOOGL", "AMZN", "MSFT",
                 "MSTR", "COIN", "HOOD", "SMCI", "MU", "SNDK", "SKHYNIX",
                 "TSM", "QCOM", "AMD", "INTC", "BABA", "PYPL", "NFLX",
                 "IBM", "CRM", "JPM", "SPY", "QQQ", "IWM", "SOXL", "SOXS",
                 "KORU", "TSLL", "TSLZ", "NVDX", "NVDL", "EWY", "RKLB",
                 "CRCL", "PLTR", "UBER", "SOFI", "ROKU", "SNAP",
                 "XAG", "COPPER", "GOLD", "SILVER", "DRAM", "BANK",
                 "TQQQ", "SQQQ", "UPRO", "SPXL", "SPXS", "CL", "INX"}
# XAU 保留（黄金也可以做主力分析）

SCAN_TOP_N = 150      # 扫成交量前 N
ALERT_N = 3           # 每次推 top N（各方向）
MIN_VOL_MM = 30       # 24h 最低成交量

# 判定门槛
PRICE_FLAT_PCT = 2.0       # 近 1 小时价格波动 < 2%（横盘）
OI_INCREASE_PCT = 3.0      # 近 1 小时 OI 增加 > 3%
LSR_EXTREME_LONG = 2.0     # 多空比 > 2.0 = 散户过度做多
LSR_EXTREME_SHORT = 0.6    # 多空比 < 0.6 = 散户过度做空
TAKER_SELL_HEAVY = 0.85    # 主动买/主动卖 < 0.85 = 主动卖占优
TAKER_BUY_HEAVY = 1.15     # 主动买/主动卖 > 1.15 = 主动买占优


def fetch(url):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(r, timeout=10).read())
    except Exception:
        return None


def is_valid(sym):
    if sym in EXCLUDE_MAJORS:
        return False
    if not sym.endswith("USDT"):
        return False
    base = sym[:-4]
    if base in STOCK_TICKERS:
        return False
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


def analyze(sym):
    """分析一个品种，返回 dict 或 None"""
    # 用 12 根 5m = 1 小时窗口
    oi = fetch(f"{BASE}/futures/data/openInterestHist?symbol={sym}&period=5m&limit=13")
    lsr = fetch(f"{BASE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=13")
    taker = fetch(f"{BASE}/futures/data/takerlongshortRatio?symbol={sym}&period=5m&limit=13")
    if not oi or not lsr or not taker or len(oi) < 12 or len(lsr) < 12 or len(taker) < 12:
        return None
    k5m = fetch(f"{BASE}/fapi/v1/klines?symbol={sym}&interval=5m&limit=13")
    if not k5m or len(k5m) < 12:
        return None

    # 1. 横盘度：近 12 根 K线的（最高 - 最低）/ 均价
    highs = [float(k[2]) for k in k5m]
    lows = [float(k[3]) for k in k5m]
    closes = [float(k[4]) for k in k5m]
    price = closes[-1]
    price_range_pct = (max(highs) - min(lows)) / (sum(closes) / len(closes)) * 100

    # 2. OI 变化
    oi_now = float(oi[-1]["sumOpenInterestValue"])
    oi_1h_ago = float(oi[0]["sumOpenInterestValue"])
    if oi_1h_ago == 0: return None
    oi_change = (oi_now - oi_1h_ago) / oi_1h_ago * 100

    # 3. 多空比（长期账户）
    lsr_now = float(lsr[-1]["longShortRatio"])
    lsr_1h_ago = float(lsr[0]["longShortRatio"])
    lsr_change = lsr_now - lsr_1h_ago

    # 4. 主动买卖比（>1 主动买占优；<1 主动卖占优）
    taker_now = float(taker[-1]["buySellRatio"])
    taker_avg = sum(float(t["buySellRatio"]) for t in taker[-6:]) / 6

    return {
        "symbol": sym,
        "short": sym[:-4],
        "price": price,
        "price_range_pct": price_range_pct,
        "oi_change": oi_change,
        "lsr_now": lsr_now,
        "lsr_change": lsr_change,
        "taker_now": taker_now,
        "taker_avg": taker_avg,
    }


def score_distribution(d):
    """给"顶部派发"打分（0-100）—— 越高越像"""
    if not d: return 0
    score = 0
    # 横盘
    if d["price_range_pct"] < PRICE_FLAT_PCT:
        score += 25
    elif d["price_range_pct"] < PRICE_FLAT_PCT * 1.5:
        score += 15
    # OI 增加
    if d["oi_change"] > OI_INCREASE_PCT:
        score += 25
    elif d["oi_change"] > 1.5:
        score += 12
    # 多空比高（散户拼命做多）
    if d["lsr_now"] > LSR_EXTREME_LONG and d["lsr_change"] > 0:
        score += 25
    elif d["lsr_now"] > 1.5:
        score += 12
    # 主动卖压
    if d["taker_now"] < TAKER_SELL_HEAVY:
        score += 25
    elif d["taker_now"] < 1.0:
        score += 12
    return score


def score_accumulation(d):
    """给"底部吸筹"打分"""
    if not d: return 0
    score = 0
    if d["price_range_pct"] < PRICE_FLAT_PCT:
        score += 25
    elif d["price_range_pct"] < PRICE_FLAT_PCT * 1.5:
        score += 15
    if d["oi_change"] > OI_INCREASE_PCT:
        score += 25
    elif d["oi_change"] > 1.5:
        score += 12
    if d["lsr_now"] < LSR_EXTREME_SHORT and d["lsr_change"] < 0:
        score += 25
    elif d["lsr_now"] < 0.8:
        score += 12
    if d["taker_now"] > TAKER_BUY_HEAVY:
        score += 25
    elif d["taker_now"] > 1.0:
        score += 12
    return score


def get_scan_pool():
    tickers = fetch(f"{BASE}/fapi/v1/ticker/24hr")
    if not tickers: return []
    ok = []
    for t in tickers:
        sym = t["symbol"]
        if not is_valid(sym): continue
        vol_mm = float(t.get("quoteVolume", 0)) / 1e6
        if vol_mm < MIN_VOL_MM: continue
        ok.append((sym, vol_mm))
    ok.sort(key=lambda x: -x[1])
    return [x[0] for x in ok[:SCAN_TOP_N]]


def build_text(distributions, accumulations):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"🎯 *主力行为扫描 · {now.strftime('%m-%d %H:%M')} BJ*"]
    lines.append("`━━━━━━━━━━━━━━━━━━━`")
    lines.append("")

    if distributions:
        lines.append("🔴 *顶部派发 · 潜在做空*")
        lines.append("_横盘 + OI↑ + 多空比↑ + 主动卖压_")
        lines.append("")
        for i, d in enumerate(distributions[:ALERT_N], 1):
            score = score_distribution(d)
            lines.append(f"`{i}` *{d['short']}* · {score}分 · {fmt_price(d['price'])}")
            lines.append(f"   横盘 {d['price_range_pct']:.2f}% · OI +{d['oi_change']:.2f}% · 多空比 {d['lsr_now']:.2f}({d['lsr_change']:+.2f}) · 买卖比 {d['taker_now']:.2f}")
            lines.append("")

    if accumulations:
        lines.append("🟢 *底部吸筹 · 潜在做多*")
        lines.append("_横盘 + OI↑ + 多空比↓ + 主动买压_")
        lines.append("")
        for i, d in enumerate(accumulations[:ALERT_N], 1):
            score = score_accumulation(d)
            lines.append(f"`{i}` *{d['short']}* · {score}分 · {fmt_price(d['price'])}")
            lines.append(f"   横盘 {d['price_range_pct']:.2f}% · OI +{d['oi_change']:.2f}% · 多空比 {d['lsr_now']:.2f}({d['lsr_change']:+.2f}) · 买卖比 {d['taker_now']:.2f}")
            lines.append("")

    if not distributions and not accumulations:
        return None

    lines.append("`━━━━━━━━━━━━━━━━━━━`")
    lines.append("_1H 窗口 · 每 5min 扫描 · 只推分≥60的_")
    return "\n".join(lines)


def send_tg(text):
    token = ALT_TG_TOKEN if ALT_TG_TOKEN else os.environ.get("TG_TOKEN", "")
    if not token:
        print("⚠️ 没配 token")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main():
    pool = get_scan_pool()
    if not pool:
        print("拉不到扫描池")
        return

    results = []
    for i, sym in enumerate(pool):
        d = analyze(sym)
        if d:
            results.append(d)
        if (i + 1) % 20 == 0:
            time.sleep(1)  # 限流

    # 分别打分
    distributions = []
    accumulations = []
    for d in results:
        sd = score_distribution(d)
        sa = score_accumulation(d)
        if sd >= 60:
            distributions.append((d, sd))
        if sa >= 60:
            accumulations.append((d, sa))

    distributions.sort(key=lambda x: -x[1])
    accumulations.sort(key=lambda x: -x[1])

    distributions_only_d = [d for d, _ in distributions]
    accumulations_only_d = [d for d, _ in accumulations]

    if not distributions_only_d and not accumulations_only_d:
        print("无信号（阈值 60）")
        return

    text = build_text(distributions_only_d, accumulations_only_d)
    if not text:
        return

    # 15 分钟去重
    state = _load_state()
    dk = today_key()
    now = datetime.datetime.utcnow()
    slot = now.hour * 4 + now.minute // 15
    key = f"smart_money:{dk}:{slot}"
    if already_fired(state, key):
        print("15min 内已推过")
        return
    mark_fired(state, key)
    _save_state(state)

    print(f"检出 顶部派发 {len(distributions_only_d)} + 底部吸筹 {len(accumulations_only_d)}")
    try:
        send_tg(text)
        print("✓ 推送成功")
    except Exception as e:
        print(f"失败: {e}")


if __name__ == "__main__":
    main()
