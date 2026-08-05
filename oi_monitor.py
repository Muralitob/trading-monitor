"""
Open Interest (OI) 异动监控 · 每 5 分钟扫描
目标：找刚突破 / 刚起势的机会

逻辑：
1. 拉合约成交量 top 100 品种（当日热门）
2. 对每个，比较当前 OI vs 15/30 分钟前的 OI
3. 判定异动：
   - "起势多"：OI +3%↑ + 价格 +1~5%（涨且加仓）
   - "起势空"：OI +3%↑ + 价格 -1~-5%（跌且加仓）
   - "空头回补"：OI -3%↓ + 价格 +1~3%（涨且减仓 = 空头认输）
   - "多头砸盘"：OI -3%↓ + 价格 -1~-3%（跌且减仓 = 多头割肉）
4. 只推榜单前 5，避免刷屏
"""
import sys, os, json, urllib.request, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    _load_state, _save_state, already_fired, mark_fired, today_key,
    push_all, send_feishu, FEISHU_WEBHOOK,
)

BASE = "https://fapi.binance.com"
TOP_N = 100                    # 扫描前 N 热门币
ALERT_N = 5                    # 每次推送前 N 异动
OI_CHANGE_MIN = 1.5            # OI 变化门槛 %（5min 窗口比 15min 敏感，降低门槛）
PRICE_CHANGE_MIN = 0.5         # 价格变化门槛 %
PRICE_CHANGE_MAX = 5.0         # 价格变化上限
LOOKBACK_MIN = 5               # 回溯 5 分钟


def fetch_json(url, timeout=10):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except Exception as e:
        return None


def get_top_symbols():
    """从 24h ticker 拿成交量 top N 的 USDT 永续合约"""
    data = fetch_json(f"{BASE}/fapi/v1/ticker/24hr")
    if not data:
        return []
    # 只要 USDT 永续
    perp = [d for d in data if d["symbol"].endswith("USDT")]
    perp.sort(key=lambda x: -float(x.get("quoteVolume", 0)))
    return perp[:TOP_N]


def get_oi_history(symbol, period="5m", limit=2):
    """拿 OI 历史数据，limit=2 = 现在 + 5 分钟前"""
    url = f"{BASE}/futures/data/openInterestHist?symbol={symbol}&period={period}&limit={limit}"
    return fetch_json(url)


def classify(oi_change_pct, price_change_pct):
    """把异动分类"""
    if oi_change_pct > 0:
        if price_change_pct > 0:
            return "起势多", "🚀", "LONG"
        elif price_change_pct < 0:
            return "起势空", "💥", "SHORT"
    else:
        if price_change_pct > 0:
            return "空头回补", "🔥", "LONG"  # 空头认输，可能延续
        elif price_change_pct < 0:
            return "多头砸盘", "🩸", "SHORT"
    return None, None, None


def scan():
    print("拉 top 100 热门币...")
    tickers = get_top_symbols()
    if not tickers:
        print("❌ 拉不到数据")
        return []

    anomalies = []
    for i, t in enumerate(tickers):
        sym = t["symbol"]
        current_price = float(t["lastPrice"])
        vol_24h = float(t["quoteVolume"]) / 1e6  # 单位百万 USDT

        oi_hist = get_oi_history(sym, "5m", 2)
        if not oi_hist or len(oi_hist) < 2:
            continue

        oi_now = float(oi_hist[-1]["sumOpenInterestValue"])
        oi_past = float(oi_hist[0]["sumOpenInterestValue"])  # 5 分钟前
        if oi_past == 0: continue

        oi_change = (oi_now - oi_past) / oi_past * 100

        # 价格 5 分钟变化
        k5m = fetch_json(f"{BASE}/fapi/v1/klines?symbol={sym}&interval=5m&limit=2")
        if not k5m or len(k5m) < 2:
            continue
        price_5m_ago = float(k5m[0][4])  # 5 分钟前收盘价
        price_change = (current_price - price_5m_ago) / price_5m_ago * 100

        # 过滤
        if abs(oi_change) < OI_CHANGE_MIN:
            continue
        if abs(price_change) < PRICE_CHANGE_MIN or abs(price_change) > PRICE_CHANGE_MAX:
            continue

        kind, icon, direction = classify(oi_change, price_change)
        if not kind:
            continue

        anomalies.append({
            "symbol": sym, "price": current_price,
            "oi_change": oi_change,
            "price_change": price_change,
            "vol_24h_mm": vol_24h,
            "kind": kind, "icon": icon, "direction": direction,
            "rank": i + 1,
        })

        # 每 30 个休息 1 秒避免限流
        if (i + 1) % 30 == 0:
            import time; time.sleep(1)

    # 按 |OI 变化| × |价格变化| 排序（综合强度）
    anomalies.sort(key=lambda x: -(abs(x["oi_change"]) * abs(x["price_change"])))
    return anomalies


def build_feishu_card(anomalies):
    """把 top 异动做成飞书卡片"""
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"扫描完成 · 前 100 热门币中检出 **{len(anomalies)}** 个异动"]
    lines.append(f"仅推榜单前 {ALERT_N}，按综合强度排序\n")

    for a in anomalies[:ALERT_N]:
        oi_c = f"<font color='green'>+{a['oi_change']:.2f}%</font>" if a['oi_change'] > 0 else f"<font color='red'>{a['oi_change']:.2f}%</font>"
        p_c = f"<font color='green'>+{a['price_change']:.2f}%</font>" if a['price_change'] > 0 else f"<font color='red'>{a['price_change']:.2f}%</font>"

        symbol_short = a['symbol'].replace('USDT', '')
        lines.append(
            f"{a['icon']} **{symbol_short}** · {a['kind']} · **{a['direction']}**\n"
            f"　现价 `${a['price']:.5g}`  ·  5minOI {oi_c}  ·  5min价格 {p_c}\n"
            f"　24h量 ${a['vol_24h_mm']:.1f}M  ·  成交量排名 #{a['rank']}"
        )
    lines.append(f"\n_{now.strftime('%m-%d %H:%M')} BJ · 每 5 分钟扫描_")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🔥 OI 异动扫描 · {now.strftime('%H:%M')}"},
            "template": "red" if any(a["direction"] == "SHORT" for a in anomalies[:3]) else "green",
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(lines)}}],
    }


def send_feishu_card(card):
    if not FEISHU_WEBHOOK:
        return
    url = FEISHU_WEBHOOK if FEISHU_WEBHOOK.startswith("http") else \
          f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_WEBHOOK}"
    payload = {"msg_type": "interactive", "card": card}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=10).read()


def main():
    state = _load_state()
    dk = today_key()

    anomalies = scan()
    if not anomalies:
        print("无异动")
        return

    # 去重：同一品种同方向 15 分钟内不重复推（5min 扫描 × 3）
    fresh = []
    now = datetime.datetime.utcnow()
    slot_15min = now.hour * 4 + now.minute // 15
    for a in anomalies:
        key = f"oi:{a['symbol']}:{a['direction']}:{dk}:{slot_15min}"
        if already_fired(state, key):
            continue
        mark_fired(state, key)
        fresh.append(a)

    if not fresh:
        print("所有异动都已推过（去重）")
        _save_state(state)
        return

    _save_state(state)

    # 只推榜单前 5
    print(f"检出 {len(fresh)} 个新异动，推前 {ALERT_N} 个")
    for a in fresh[:ALERT_N]:
        print(f"  {a['icon']} {a['symbol']:<15} {a['kind']:<8} OI{a['oi_change']:+.2f}% 价格{a['price_change']:+.2f}%")

    card = build_feishu_card(fresh)
    if FEISHU_WEBHOOK:
        try:
            send_feishu_card(card)
            print("✓ 飞书推送成功")
        except Exception as e:
            print(f"飞书推送失败: {e}")


if __name__ == "__main__":
    main()
