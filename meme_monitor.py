"""
Robinhood Chain MEME 扫描器
每 15 min 扫 CoinGecko 上 Robinhood Chain meme 榜前 40
Bruce Ma 战法：Anchored VWAP × POC 双共振买点

买点 = 现价同时靠近 (从24h高点锚定的 VWAP) 和 (24h 成交量 POC)
      + 最近 1h 没在崩盘
"""
import os, sys, json, urllib.request, urllib.parse, datetime, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import _load_state, _save_state, already_fired, mark_fired, today_key, CHAT_ID

CG_BASE = "https://api.coingecko.com/api/v3"
ALT_TG_TOKEN = os.environ.get("ALT_TG_TOKEN", "").strip()

# 过滤门槛
MIN_MCAP_USD = 10_000_000    # 最低市值 $10M（避免流动性差）
MIN_24H_VOL_USD = 500_000    # 最低 24h 成交 $500K
TOP_N = 40                   # 扫前 40

# 买点判定
VWAP_TOL_PCT = 4.0           # 现价在 VWAP ±4%（承接区）
POC_TOL_PCT = 4.0            # 现价在 POC ±4%
FREEFALL_1H = -8.0           # 最近 1h 跌 >8% 不推（还在自由落体）


def cg_fetch(path, params=None):
    url = f"{CG_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        print(f"[cg] {url}: {e}")
        return None


def get_top_memes():
    data = cg_fetch("/coins/markets", {
        "vs_currency": "usd",
        "category": "robinhood-chain-meme",
        "order": "market_cap_desc",
        "per_page": TOP_N,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h",
    })
    if not data: return []
    ok = []
    for c in data:
        mc = c.get("market_cap") or 0
        v = c.get("total_volume") or 0
        if mc < MIN_MCAP_USD or v < MIN_24H_VOL_USD:
            continue
        ok.append(c)
    return ok


def fetch_ohlc_24h(coin_id):
    """5-min 精度的 24h 价格+成交量"""
    data = cg_fetch(f"/coins/{coin_id}/market_chart", {"vs_currency": "usd", "days": "1"})
    if not data: return None, None
    return data.get("prices", []), data.get("total_volumes", [])


def anchored_vwap(prices, volumes, anchor_idx=0):
    if anchor_idx >= len(prices): return None
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(anchor_idx, len(prices)):
        p = prices[i][1]
        v = volumes[i][1] if i < len(volumes) else 0
        cum_pv += p * v
        cum_v += v
    return (cum_pv / cum_v) if cum_v > 0 else None


def compute_poc(prices, volumes, bins=30):
    if not prices: return None
    p_min = min(p for _, p in prices)
    p_max = max(p for _, p in prices)
    if p_max <= p_min: return None
    bw = (p_max - p_min) / bins
    buckets = [0.0] * bins
    for i, (_, p) in enumerate(prices):
        v = volumes[i][1] if i < len(volumes) else 0
        b = min(int((p - p_min) / bw), bins - 1)
        buckets[b] += v
    top_b = max(range(bins), key=lambda i: buckets[i])
    return p_min + (top_b + 0.5) * bw


def analyze(coin):
    coin_id = coin["id"]
    prices, volumes = fetch_ohlc_24h(coin_id)
    if not prices or len(prices) < 20:
        return None

    cur = prices[-1][1]

    # 24h 高点作为 VWAP 锚点（假设那是一次拉升起点）
    hi_idx = max(range(len(prices)), key=lambda i: prices[i][1])
    vwap = anchored_vwap(prices, volumes, anchor_idx=hi_idx)
    if not vwap: return None

    poc = compute_poc(prices, volumes)
    if not poc: return None

    vwap_dist = (cur - vwap) / vwap * 100
    poc_dist = (cur - poc) / poc * 100

    # 现价必须在 VWAP 附近（下方 8% 到上方 4%）
    if not (-VWAP_TOL_PCT * 2 < vwap_dist < VWAP_TOL_PCT):
        return None
    # POC 附近
    if not (-POC_TOL_PCT * 2 < poc_dist < POC_TOL_PCT):
        return None
    # 最近 1h 不能崩得太快
    change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
    if change_1h < FREEFALL_1H:
        return None

    return {
        "coin_id": coin_id,
        "symbol": coin["symbol"].upper(),
        "name": coin["name"],
        "price": cur,
        "market_cap": coin.get("market_cap", 0),
        "vol_24h": coin.get("total_volume", 0),
        "change_1h": change_1h,
        "change_24h": coin.get("price_change_percentage_24h_in_currency") or 0,
        "vwap": vwap,
        "poc": poc,
        "vwap_dist_pct": vwap_dist,
        "poc_dist_pct": poc_dist,
    }


def fmt_num(v):
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    return f"${v/1e3:.0f}K"


def fmt_price(v):
    if v >= 1: return f"${v:.4g}"
    if v >= 0.01: return f"${v:.4f}"
    return f"${v:.6g}"


def build_msg(sigs):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"🎯 *Robinhood Chain MEME 买点 · {now.strftime('%m-%d %H:%M')} BJ*"]
    lines.append("`━━━━━━━━━━━━━━━━━━━`")
    lines.append("_VWAP × POC 双共振 · Bruce Ma 战法_")
    lines.append("")
    for i, s in enumerate(sigs, 1):
        lines.append(f"`{i}` *{s['symbol']}* · {s['name']}")
        lines.append(f"   现价 {fmt_price(s['price'])}  MCap {fmt_num(s['market_cap'])}  Vol24h {fmt_num(s['vol_24h'])}")
        lines.append(f"   VWAP {fmt_price(s['vwap'])} ({s['vwap_dist_pct']:+.1f}%) · POC {fmt_price(s['poc'])} ({s['poc_dist_pct']:+.1f}%)")
        lines.append(f"   1h {s['change_1h']:+.1f}%  ·  24h {s['change_24h']:+.1f}%")
        lines.append("")
    lines.append("`━━━━━━━━━━━━━━━━━━━`")
    lines.append("_$50 试仓 · 翻倍出本 · 跌破成本区反抽站不回=止损_")
    return "\n".join(lines)


def send_tg(text):
    token = ALT_TG_TOKEN if ALT_TG_TOKEN else os.environ.get("TG_TOKEN", "")
    if not token:
        print("⚠️ 没配 token"); return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main():
    memes = get_top_memes()
    if not memes:
        print("拉不到 memes 列表"); return
    print(f"扫 {len(memes)} 个候选")

    sigs = []
    for i, coin in enumerate(memes):
        try:
            r = analyze(coin)
            if r:
                sigs.append(r)
        except Exception as e:
            print(f"[analyze:{coin['id']}] {e}")
        # CoinGecko 免费 30req/min，控速
        time.sleep(0.3)
        if (i + 1) % 10 == 0:
            time.sleep(2)

    if not sigs:
        print("无信号")
        return

    # 按 VWAP + POC 距离和排序（越接近越好）
    sigs.sort(key=lambda s: abs(s["vwap_dist_pct"]) + abs(s["poc_dist_pct"]))
    sigs = sigs[:6]

    # 30 分钟去重
    state = _load_state()
    now = datetime.datetime.utcnow()
    slot = now.hour * 2 + now.minute // 30
    key = f"meme_scan:{today_key()}:{slot}"
    if already_fired(state, key):
        print("30 min 内已推过"); return
    mark_fired(state, key)
    _save_state(state)

    text = build_msg(sigs)
    try:
        send_tg(text)
        print(f"✓ 推送 {len(sigs)} 个买点")
    except Exception as e:
        print(f"推送失败: {e}")


if __name__ == "__main__":
    main()
