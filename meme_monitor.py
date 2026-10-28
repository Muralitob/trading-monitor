"""
Robinhood Chain MEME 扫描器
每 15 min 扫 CoinGecko 上 Robinhood Chain meme 榜前 40
Bruce Ma 战法：Anchored VWAP × POC 双共振买点

买点 = 现价同时靠近 (从24h高点锚定的 VWAP) 和 (24h 成交量 POC)
      + 最近 1h 没在崩盘
"""
import os, sys, json, urllib.request, urllib.parse, datetime, time, uuid, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import _load_state, _save_state, already_fired, mark_fired, today_key, CHAT_ID

CG_BASE = "https://api.coingecko.com/api/v3"
# 优先用 MEME_TG_TOKEN（My_Trader bot），没配就 fallback 到 alt bot
MEME_TG_TOKEN = os.environ.get("MEME_TG_TOKEN", "").strip()
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
        "_prices": prices,
        "_volumes": volumes,
        "_hi_idx": hi_idx,
    }


def draw_chart(sig):
    """画 24h 价格 + VWAP + POC + 成交量分布"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    BG = "#131722"; GRID = "#1e222d"; TEXT = "#d1d4dc"
    GREEN = "#26a69a"; RED = "#ef5350"; YELLOW = "#f5c542"; BLUE = "#4A90E2"; ORANGE = "#ff9800"

    prices = sig["_prices"]
    volumes = sig["_volumes"]
    hi_idx = sig["_hi_idx"]
    vwap = sig["vwap"]; poc = sig["poc"]; cur = sig["price"]

    xs = list(range(len(prices)))
    ys = [p for _, p in prices]

    # 时间标签
    times = [datetime.datetime.utcfromtimestamp(t / 1000) + datetime.timedelta(hours=8) for t, _ in prices]

    fig, (ax, ax_vp) = plt.subplots(1, 2, figsize=(12, 6), dpi=90,
                                     gridspec_kw={"width_ratios": [4, 1], "wspace": 0.02})
    fig.patch.set_facecolor(BG)
    for a in (ax, ax_vp):
        a.set_facecolor(BG)
        a.tick_params(colors=TEXT, labelsize=8)
        a.grid(True, color=GRID, linewidth=0.5, alpha=0.5)
        for sp in a.spines.values():
            sp.set_color(GRID)

    # 主图：价格线
    ax.plot(xs, ys, color=BLUE, linewidth=1.5, alpha=0.9, label="Price")

    # 从 hi_idx 开始的 anchored VWAP 线（每一点重新计算）
    cum_pv = 0.0; cum_v = 0.0
    vwap_line = [None] * len(prices)
    for i in range(hi_idx, len(prices)):
        p = prices[i][1]
        v = volumes[i][1] if i < len(volumes) else 0
        cum_pv += p * v
        cum_v += v
        if cum_v > 0:
            vwap_line[i] = cum_pv / cum_v
    valid_x = [x for x in xs if vwap_line[x] is not None]
    valid_y = [vwap_line[x] for x in valid_x]
    ax.plot(valid_x, valid_y, color=YELLOW, linewidth=1.6, label=f"aVWAP {vwap:.4g}")

    # 锚点标记
    ax.axvline(x=hi_idx, color=RED, linewidth=0.6, linestyle=":", alpha=0.6)
    ax.annotate("anchor (24h high)", xy=(hi_idx, prices[hi_idx][1]),
                xytext=(3, 5), textcoords="offset points",
                color=RED, fontsize=8)

    # POC 水平线
    ax.axhline(y=poc, color=ORANGE, linewidth=1.4, linestyle="--", alpha=0.9, label=f"POC {poc:.4g}")

    # 现价
    ax.axhline(y=cur, color=GREEN, linewidth=0.8, linestyle="-", alpha=0.6)
    ax.annotate(f"NOW {cur:.4g}", xy=(len(xs) - 1, cur),
                xytext=(5, 0), textcoords="offset points",
                color=GREEN, fontsize=9, fontweight="bold")

    # X 轴时间
    n = len(xs)
    step = max(1, n // 8)
    xticks = list(range(0, n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%H:%M") for i in xticks], color=TEXT)
    ax.set_xlim(0, n + 4)

    ax.set_title(f"{sig['symbol']} · {sig['name']} · 24h · VWAP × POC",
                 color=TEXT, fontsize=10, loc="left")
    ax.legend(loc="upper right", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=8)

    # 右侧：成交量分布直方图
    p_min = min(ys); p_max = max(ys)
    bins = 30
    bw = (p_max - p_min) / bins if p_max > p_min else 1
    buckets = [0.0] * bins
    for i, (_, p) in enumerate(prices):
        v = volumes[i][1] if i < len(volumes) else 0
        b = min(int((p - p_min) / bw), bins - 1)
        buckets[b] += v
    y_centers = [p_min + (i + 0.5) * bw for i in range(bins)]
    # 高亮 POC 所在的 bin
    top_b = max(range(bins), key=lambda i: buckets[i])
    colors = [ORANGE if i == top_b else BLUE for i in range(bins)]
    ax_vp.barh(y_centers, buckets, height=bw * 0.9, color=colors, alpha=0.75)
    ax_vp.set_ylim(ax.get_ylim())
    ax_vp.set_xticks([])
    ax_vp.set_yticks([])
    ax_vp.set_title("Volume", color=TEXT, fontsize=8, loc="left")

    plt.tight_layout()
    out_path = os.path.join(tempfile.gettempdir(),
                             f"meme_{sig['symbol']}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path


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


def _tg_token():
    return MEME_TG_TOKEN or ALT_TG_TOKEN or os.environ.get("TG_TOKEN", "")


def send_tg(text):
    token = _tg_token()
    if not token:
        print("⚠️ 没配 token"); return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def send_tg_photo(caption, photo_path):
    token = _tg_token()
    if not token:
        print("⚠️ 没配 token"); return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    boundary = uuid.uuid4().hex
    with open(photo_path, "rb") as f:
        photo = f.read()

    def part(name, value, filename=None, content_type=None):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        if filename: head += f'; filename="{filename}"'
        head += "\r\n"
        if content_type: head += f"Content-Type: {content_type}\r\n"
        head += "\r\n"
        if isinstance(value, str): value = value.encode()
        return head.encode() + value + b"\r\n"

    body = b""
    body += part("chat_id", str(CHAT_ID))
    body += part("caption", caption)
    body += part("parse_mode", "Markdown")
    body += part("photo", photo, filename="chart.png", content_type="image/png")
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req, timeout=20).read()


def build_single_caption(s):
    """单币种带图消息文案"""
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"🎯 *{s['symbol']}* · {s['name']}"]
    lines.append(f"_Robinhood Chain · VWAP × POC 共振_ · {now.strftime('%m-%d %H:%M')} BJ")
    lines.append("")
    lines.append(f"💵 现价 `{fmt_price(s['price'])}`")
    lines.append(f"📊 MCap {fmt_num(s['market_cap'])}  ·  Vol24h {fmt_num(s['vol_24h'])}")
    lines.append(f"📈 1h {s['change_1h']:+.1f}%  ·  24h {s['change_24h']:+.1f}%")
    lines.append("")
    lines.append("🧲 *成本共振区*")
    lines.append(f"   aVWAP `{fmt_price(s['vwap'])}` ({s['vwap_dist_pct']:+.1f}%)")
    lines.append(f"   POC   `{fmt_price(s['poc'])}` ({s['poc_dist_pct']:+.1f}%)")
    lines.append("")
    lines.append(f"🎯 *$50 试仓 · 翻倍出本*")
    lines.append(f"🛑 跌破成本区反抽站不回 = 止损")
    return "\n".join(lines)


def main():
    # 2026-10-28 停用：用户只要追龙频道，不要链上 MEME 推送（VPS cron 还在，跑到这里直接退出）
    if not os.environ.get("MEME_ENABLED"):
        print("meme monitor 已停用"); return
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

    # 单币种 30 分钟去重（避免同币重复推）
    state = _load_state()
    now = datetime.datetime.utcnow()
    slot = now.hour * 2 + now.minute // 30
    dk = today_key()

    pushed = 0
    for s in sigs:
        key = f"meme:{s['symbol']}:{dk}:{slot}"
        if already_fired(state, key):
            continue
        mark_fired(state, key)
        caption = build_single_caption(s)
        try:
            png = draw_chart(s)
            send_tg_photo(caption, png)
            try: os.remove(png)
            except: pass
            pushed += 1
        except Exception as e:
            print(f"[push:{s['symbol']}] {e}")
            try:
                send_tg(caption)
                pushed += 1
            except Exception as e2:
                print(f"[push text:{s['symbol']}] {e2}")

    _save_state(state)
    print(f"✓ 推送 {pushed}/{len(sigs)} 个买点")


if __name__ == "__main__":
    main()
