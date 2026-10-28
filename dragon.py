"""
追龙扫描器（替换原来的山寨异动频道，run_oi.sh 每 5 分钟触发）

1. 选龙（每天 UTC 0 点日线收盘后）：成交量前 150 的 USDT 永续里，离 14 日高点回撤 ≤25%，7 日涨幅前 3 且 ≥20%
   → 推「龙头榜」：新上榜 / 还在榜 / 掉榜
2. 等上车（每根 4H 收盘后）：龙上榜后 7 天内，4H 回踩 EMA50（最低价碰到、收盘站上）
   + EMA50 向上 + 阳线且下影线 ≥ 实体 → 推「上车提醒」，止损=这根低点-0.2ATR，目标 2R
3. 下车（每根 4H 收盘后）：连续 2 根 4H 收在 EMA50 下方 = 龙死了；放量滞涨 = 警告

回测（2025-09 ~ 2026-10，当前成交量前 110 的币，有幸存者偏差，含 0.1% 手续费）：
  只买 7 日涨幅前 3：下周中位数 -6.8%，只有 38% 继续涨（帖子说的"龙头继续涨"不成立）
  加回撤 ≤25% 过滤后：直接追 330 笔 平均 +0.05R；等回踩 135 笔 胜率 35% 平均 +0.02R
  两种都是前半段亏、后半段赚 → 没有可靠优势，只当观察信号
"""
import os
import json
import time
import datetime
import urllib.request
from pathlib import Path

BASE = "https://fapi.binance.com"
TG_TOKEN = os.environ.get("ALT_TG_TOKEN", "").strip() or os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
STATE_FILE = Path(__file__).parent / "dragon_state.json"

STOCK_TICKERS = {"NVDA", "TSLA", "META", "AAPL", "GOOGL", "AMZN", "MSFT",
                 "MSTR", "COIN", "HOOD", "SMCI", "MU", "SNDK", "SKHYNIX",
                 "TSM", "QCOM", "AMD", "INTC", "BABA", "PYPL", "NFLX",
                 "IBM", "CRM", "JPM", "SPY", "QQQ", "IWM", "SOXL", "SOXS",
                 "KORU", "TSLL", "TSLZ", "NVDX", "NVDL", "EWY", "RKLB",
                 "CRCL", "PLTR", "UBER", "SOFI", "ROKU", "SNAP",
                 "XAG", "XAU", "COPPER", "GOLD", "SILVER", "DRAM", "BANK",
                 "TQQQ", "SQQQ", "UPRO", "SPXL", "SPXS", "CL", "INX"}

POOL_N = 150            # 扫成交量前 N
MIN_VOL = 20e6          # 24h 成交额下限
TOP_N = 3               # 每天选几条龙
MIN_R7 = 0.20           # 7 日涨幅门槛
MAX_DD = 0.25           # 离 14 日高点最多回撤（龙头回调最浅）
WATCH_DAYS = 7          # 上榜后盯几天回踩
RISK_U = 8              # 规则 v2：白名单外只能按观察信号 8U 风险算仓位


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def send_tg(text):
    if not TG_TOKEN:
        return
    data = json.dumps({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                                 data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"dragons": {}, "fired": {}}


def save_state(state):
    cutoff = time.time() - 14 * 86400
    state["fired"] = {k: v for k, v in state["fired"].items() if v > cutoff}
    STATE_FILE.write_text(json.dumps(state, indent=1))


def ema(xs, p):
    k = 2 / (p + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def fmt(p):
    return f"{p:,.1f}" if p >= 1000 else f"{p:.4g}"


def is_valid(sym):
    base = sym[:-4]
    return sym.endswith("USDT") and base not in STOCK_TICKERS and len(base) <= 12 and sym != "BTCUSDT"


# ==================== 1. 选龙 ====================

def pick_dragons():
    tickers = fetch("/fapi/v1/ticker/24hr")
    pool = [t for t in tickers if is_valid(t["symbol"]) and float(t["quoteVolume"]) >= MIN_VOL]
    pool.sort(key=lambda t: -float(t["quoteVolume"]))
    btc = fetch("/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit=9")[:-1]
    btc_r7 = float(btc[-1][4]) / float(btc[-8][4]) - 1

    rows = []
    for i, t in enumerate(pool[:POOL_N]):
        sym = t["symbol"]
        try:
            k = fetch(f"/fapi/v1/klines?symbol={sym}&interval=1d&limit=31")[:-1]   # 去掉未收盘
        except Exception as e:
            print(f"[{sym}] {e}")
            continue
        if len(k) < 15:
            continue
        closes = [float(x[4]) for x in k]
        highs = [float(x[2]) for x in k]
        vols = [float(x[7]) for x in k]
        r7 = closes[-1] / closes[-8] - 1
        rows.append({
            "symbol": sym, "r7": r7, "r3": closes[-1] / closes[-4] - 1,
            "dd": 1 - closes[-1] / max(highs[-14:]),
            "vol_x": (sum(vols[-3:]) / 3) / (sum(vols) / len(vols)),
            "vol_24h": float(t["quoteVolume"]), "price": closes[-1],
        })
        if (i + 1) % 20 == 0:
            time.sleep(1)
    rows = [r for r in rows if r["dd"] <= MAX_DD]
    rows.sort(key=lambda r: -r["r7"])
    return [r for r in rows[:TOP_N] if r["r7"] >= MIN_R7], btc_r7


def daily_board(state):
    dragons, btc_r7 = pick_dragons()
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    tracked = state["dragons"]
    new_syms = {d["symbol"] for d in dragons}

    lines = [f"🐲 追龙 · 龙头榜 {today}", f"BTC 7 日 {btc_r7*100:+.1f}%", ""]
    if not dragons:
        lines.append("今天没有 7 日涨幅 ≥20% 的龙，市场没主线 → 空仓等")
    for i, d in enumerate(dragons, 1):
        tag = "🆕 新上榜" if d["symbol"] not in tracked else "🔁 还在榜"
        lines.append(f"{i}. {d['symbol'][:-4]}  {tag}")
        lines.append(f"   7日 {d['r7']*100:+.0f}%  3日 {d['r3']*100:+.0f}%  离14日高 -{d['dd']*100:.0f}%  "
                     f"量能 {d['vol_x']:.1f}x  成交 ${d['vol_24h']/1e6:.0f}M")
        tracked[d["symbol"]] = {"since": tracked.get(d["symbol"], {}).get("since", today), "last_seen": today}

    dropped = [s for s in tracked if s not in new_syms]
    for s in dropped:
        last = datetime.date.fromisoformat(tracked[s]["last_seen"])
        if (datetime.date.fromisoformat(today) - last).days > WATCH_DAYS:
            del tracked[s]          # 7 天没回榜，不再盯
    if dropped:
        lines.append("")
        lines.append("📉 掉出榜：" + "、".join(s[:-4] for s in dropped) + "（还会盯回踩到上榜后第 7 天）")

    lines += ["", "上车规则：等 4H 回踩 EMA50 + 下影线确认，不追高", "回测没有可靠优势，白名单外 = 观察信号，风险 8U"]
    send_tg("\n".join(lines))
    print("\n".join(lines))


# ==================== 2/3. 4H 上车 / 下车 ====================

def check_4h(state):
    for sym in list(state["dragons"]):
        try:
            k = fetch(f"/fapi/v1/klines?symbol={sym}&interval=4h&limit=120")[:-1]
        except Exception as e:
            print(f"[{sym}] {e}")
            continue
        bar_ts = k[-1][0]
        o, h, l, c = (float(v) for v in k[-1][1:5])
        closes = [float(x[4]) for x in k]
        vols = [float(x[7]) for x in k]
        e50 = ema(closes, 50)
        trs = [float(x[2]) - float(x[3]) for x in k]
        atr = sum(trs[-14:]) / 14
        name = sym[:-4]

        # 上车：回踩 EMA50 + 下影线确认
        body, wick = abs(c - o), min(o, c) - l
        if l <= e50[-1] < c and e50[-1] > e50[-7] and c > o and wick >= body:
            key = f"entry:{sym}:{bar_ts}"
            if key not in state["fired"]:
                state["fired"][key] = time.time()
                stop = l - 0.2 * atr
                risk_pct = (c - stop) / c
                notional = RISK_U / risk_pct
                send_tg("\n".join([
                    f"🐲 {name} 回踩上车点（观察）",
                    f"4H 回踩 EMA50 {fmt(e50[-1])} 收回，下影线确认",
                    f"入场 {fmt(c)}  止损 {fmt(stop)}（-{risk_pct*100:.1f}%）  目标 2R {fmt(c + 2*(c-stop))}",
                    f"按 {RISK_U}U 风险：名义仓位 {notional:.0f}U",
                    "",
                    "回测：等回踩 胜率 35%、平均 +0.02R，没有可靠优势 → 只用观察仓",
                    "打损就走，不补仓不摊平",
                ]))
                print(f"entry {sym}")

        # 下车：连续 2 根收在 EMA50 下方 = 龙死了
        if closes[-1] < e50[-1] and closes[-2] < e50[-2]:
            key = f"dead:{sym}:{state['dragons'][sym]['since']}"
            if key not in state["fired"]:
                state["fired"][key] = time.time()
                send_tg(f"🪦 {name} 龙死了：连续 2 根 4H 收在 EMA50 {fmt(e50[-1])} 下方\n"
                        f"有仓位就走，别守；也别去买它的跟风币")
                print(f"dead {sym}")
            del state["dragons"][sym]
            continue

        # 放量滞涨：近 3 根量 > 30 根均量 2 倍，但价格 3 根内没走出 0.5 ATR
        if sum(vols[-3:]) / 3 > 2 * sum(vols[-33:-3]) / 30 and abs(closes[-1] - closes[-4]) < 0.5 * atr:
            key = f"stall:{sym}:{bar_ts}"
            if key not in state["fired"]:
                state["fired"][key] = time.time()
                send_tg(f"⚠️ {name} 放量滞涨：近 3 根 4H 量是均量 2 倍以上，价格没走\n"
                        f"有仓位考虑减仓/收紧止损，没仓位别进")


def main():
    now = datetime.datetime.utcnow()
    state = load_state()
    # 只在收盘后 10 分钟内跑（cron 每 5 分钟，同一根 K 线用 fired 去重）
    if now.minute < 10 and now.hour % 4 == 0:
        if now.hour == 0:
            key = f"board:{now:%Y-%m-%d}"
            if key not in state["fired"]:
                state["fired"][key] = time.time()
                daily_board(state)
        check_4h(state)
    save_state(state)


if __name__ == "__main__":
    import sys
    if "--board" in sys.argv:          # 手动看今天的龙头榜（不推送）
        TG_TOKEN = ""
        daily_board({"dragons": {}, "fired": {}})
    else:
        main()
