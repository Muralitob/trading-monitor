"""
OI + 第一波突破扫描器 · 每 5 分钟

分析顺序（对齐用户框架）：
1. 日线趋势 — 只做"刚突破"，不追"已大涨"
2. 流通量代理 — 用 24h 成交量筛选甜蜜区（$50M-5B）
3. OI 结构 — OI 涨幅 vs 价格涨幅匹配度
4. 成交量放大 — 当前 5m vs 20 根均值
5. 综合打分 → 只推榜单前 5

口诀：趋势先行，市值定空间；OI 看成本，资金看方向；只做第一波，不赌第二波。
"""
import sys, os, json, urllib.request, datetime, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from monitor import (
    _load_state, _save_state, already_fired, mark_fired, today_key,
    FEISHU_WEBHOOK,
)

BASE = "https://fapi.binance.com"

# ========== 参数 ==========
TOP_N_SCAN = 150               # 扫描池 = 前 N 热门币
ALERT_N = 5                    # 推送 top N

# 门槛（5min 窗口）
OI_MIN = 1.0                   # OI 变化 %
PRICE_MIN = 0.5                # 价格变化 %

# 筛"第一波"用
VOL_24H_MIN_MM = 50            # 24h 成交量最低 50M（低于此=没资金推动）
VOL_24H_MAX_MM = 5000          # 24h 成交量最高 5B（BTC/ETH 太大推不动）
CHANGE_24H_MAX = 15            # 24h 涨/跌幅上限（超过=已经跑，不追）
DIST_20D_HIGH_MAX = 8          # 距 20日高点 < 8% = 突破区
VOL_SPIKE_MIN = 1.5            # 当前 5m 量 > 20根均值的 1.5 倍


def fetch(url, timeout=10):
    try:
        r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        return json.loads(urllib.request.urlopen(r, timeout=timeout).read())
    except Exception:
        return None


def get_top_symbols():
    data = fetch(f"{BASE}/fapi/v1/ticker/24hr")
    if not data: return []
    perp = [d for d in data if d["symbol"].endswith("USDT")]
    perp.sort(key=lambda x: -float(x.get("quoteVolume", 0)))
    return perp[:TOP_N_SCAN]


def get_oi_history(symbol, period="5m", limit=2):
    return fetch(f"{BASE}/futures/data/openInterestHist?symbol={symbol}&period={period}&limit={limit}")


def analyze_symbol(t):
    """分析单个品种，返回评分和信号 dict"""
    sym = t["symbol"]
    price = float(t["lastPrice"])
    vol_24h_mm = float(t["quoteVolume"]) / 1e6
    change_24h = float(t["priceChangePercent"])
    high_24h = float(t["highPrice"])
    low_24h = float(t["lowPrice"])

    # 【预筛】24h 成交量甜蜜区
    if vol_24h_mm < VOL_24H_MIN_MM or vol_24h_mm > VOL_24H_MAX_MM:
        return None
    # 【预筛】24h 变化不能超限（不追已大涨）
    if abs(change_24h) > CHANGE_24H_MAX:
        return None

    # 拉 OI 历史
    oi_hist = get_oi_history(sym, "5m", 2)
    if not oi_hist or len(oi_hist) < 2:
        return None
    oi_now = float(oi_hist[-1]["sumOpenInterestValue"])
    oi_past = float(oi_hist[0]["sumOpenInterestValue"])
    if oi_past == 0: return None
    oi_change = (oi_now - oi_past) / oi_past * 100

    if abs(oi_change) < OI_MIN:
        return None

    # 5min 价格变化 + 成交量数据
    k5m = fetch(f"{BASE}/fapi/v1/klines?symbol={sym}&interval=5m&limit=21")
    if not k5m or len(k5m) < 21:
        return None
    prev_close = float(k5m[-2][4])  # 上一根 5m 收盘（即"5min 前"）
    price_change = (price - prev_close) / prev_close * 100

    if abs(price_change) < PRICE_MIN:
        return None

    # 当前 5m 成交量 vs 前 20 根均值（成交量放大）
    current_vol = float(k5m[-1][7])
    avg_vol = sum(float(k[7]) for k in k5m[-21:-1]) / 20
    vol_spike = current_vol / avg_vol if avg_vol > 0 else 0

    # 日线数据判断"刚突破"
    k1d = fetch(f"{BASE}/fapi/v1/klines?symbol={sym}&interval=1d&limit=22")
    if not k1d or len(k1d) < 22:
        return None
    high_20d = max(float(k[2]) for k in k1d[-21:-1])  # 过去 20 日不含今天
    low_20d = min(float(k[3]) for k in k1d[-21:-1])
    close_20d_avg = sum(float(k[4]) for k in k1d[-21:-1]) / 20

    # 距 20日高点百分比
    dist_20d_high = (high_20d - price) / high_20d * 100
    dist_20d_low = (price - low_20d) / low_20d * 100

    # 方向 & 类型
    if oi_change > 0 and price_change > 0:
        kind, icon, direction = "起势多", "🚀", "LONG"
    elif oi_change > 0 and price_change < 0:
        kind, icon, direction = "起势空", "💥", "SHORT"
    elif oi_change < 0 and price_change > 0:
        kind, icon, direction = "空头回补", "🔥", "LONG"
    elif oi_change < 0 and price_change < 0:
        kind, icon, direction = "多头砸盘", "🩸", "SHORT"
    else:
        return None

    # ========== 综合打分 ==========
    score = 0
    reasons = []

    # 1. 日线趋势 - 刚突破（越接近 20日高越好，但没突破）
    if direction == "LONG":
        if dist_20d_high < DIST_20D_HIGH_MAX:
            score += 30
            reasons.append(f"刚接近/突破20日高 (差{dist_20d_high:.1f}%)")
        elif dist_20d_high < 15:
            score += 10
    else:  # SHORT
        if dist_20d_low < DIST_20D_HIGH_MAX:
            score += 30
            reasons.append(f"刚接近/跌破20日低 (差{dist_20d_low:.1f}%)")

    # 2. OI/价格 比例健康度（OI 涨幅 > 价格涨幅 = 主力建仓中）
    oi_price_ratio = abs(oi_change) / abs(price_change) if price_change else 0
    if 1.5 <= oi_price_ratio <= 5:
        score += 25
        reasons.append(f"OI增速>价格 (比{oi_price_ratio:.1f}x)")
    elif 0.7 <= oi_price_ratio < 1.5:
        score += 15
        reasons.append(f"OI/价同步")
    # 比 <0.7 说明拉盘无跟随（诱多/诱空），不加分

    # 3. 成交量放大
    if vol_spike > VOL_SPIKE_MIN:
        score += 25
        reasons.append(f"量能{vol_spike:.1f}x")
    elif vol_spike > 1:
        score += 10

    # 4. 24h 未大涨
    if abs(change_24h) < 5:
        score += 10
        reasons.append(f"24h{change_24h:+.1f}% (未过热)")
    elif abs(change_24h) < 10:
        score += 5

    # 5. 甜蜜市值区
    if 100 <= vol_24h_mm <= 1000:
        score += 10
        reasons.append(f"甜蜜量${vol_24h_mm:.0f}M")

    return {
        "symbol": sym, "price": price,
        "oi_change": oi_change, "price_change": price_change,
        "vol_24h_mm": vol_24h_mm, "change_24h": change_24h,
        "vol_spike": vol_spike, "oi_price_ratio": oi_price_ratio,
        "dist_20d_high": dist_20d_high, "dist_20d_low": dist_20d_low,
        "kind": kind, "icon": icon, "direction": direction,
        "score": score, "reasons": reasons,
    }


def scan():
    tickers = get_top_symbols()
    if not tickers:
        return []

    results = []
    for i, t in enumerate(tickers):
        r = analyze_symbol(t)
        if r and r["score"] >= 40:  # 最低门槛
            results.append(r)
        if (i + 1) % 30 == 0:
            time.sleep(1)

    results.sort(key=lambda x: -x["score"])
    return results


def build_card(anomalies):
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    lines = [f"扫描 {TOP_N_SCAN} 热门币，符合\"第一波\"标准的 **{len(anomalies)}** 个"]
    lines.append(f"只推榜单前 {ALERT_N} · 综合打分排序\n")

    for i, a in enumerate(anomalies[:ALERT_N], 1):
        sym = a["symbol"].replace("USDT", "")
        oi_c = f"<font color='green'>+{a['oi_change']:.2f}%</font>" if a['oi_change'] > 0 else f"<font color='red'>{a['oi_change']:.2f}%</font>"
        p_c = f"<font color='green'>+{a['price_change']:.2f}%</font>" if a['price_change'] > 0 else f"<font color='red'>{a['price_change']:.2f}%</font>"

        # 综合评分颜色
        if a["score"] >= 80:
            score_c = f"<font color='green'>**{a['score']}分**</font>"
        elif a["score"] >= 60:
            score_c = f"<font color='orange'>**{a['score']}分**</font>"
        else:
            score_c = f"<font color='grey'>{a['score']}分</font>"

        lines.append(
            f"**#{i} {a['icon']} {sym}** · {a['kind']} · **{a['direction']}** · {score_c}\n"
            f"　现价 `${a['price']:.5g}`  5minOI {oi_c}  5min价 {p_c}\n"
            f"　评分依据：{' / '.join(a['reasons'])}\n"
            f"　24h量 ${a['vol_24h_mm']:.0f}M  ·  24h涨跌 {a['change_24h']:+.1f}%  ·  量能 {a['vol_spike']:.1f}x"
        )

    lines.append(f"\n_{now.strftime('%m-%d %H:%M')} BJ · 只做第一波，不赌第二波_")

    template = "green" if anomalies and anomalies[0]["direction"] == "LONG" else "red"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🎯 第一波扫描 · {now.strftime('%H:%M')}"},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "\n\n".join(lines)}}],
    }


def send_card(card):
    if not FEISHU_WEBHOOK: return
    url = FEISHU_WEBHOOK if FEISHU_WEBHOOK.startswith("http") else \
          f"https://open.feishu.cn/open-apis/bot/v2/hook/{FEISHU_WEBHOOK}"
    payload = {"msg_type": "interactive", "card": card}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main():
    state = _load_state()
    dk = today_key()
    now = datetime.datetime.utcnow()

    results = scan()
    if not results:
        print("无符合条件的机会")
        return

    # 去重：同一品种同方向 15 分钟内不重复推
    slot = now.hour * 4 + now.minute // 15
    fresh = []
    for r in results:
        key = f"first_wave:{r['symbol']}:{r['direction']}:{dk}:{slot}"
        if already_fired(state, key):
            continue
        mark_fired(state, key)
        fresh.append(r)

    _save_state(state)

    if not fresh:
        print("所有信号都已推过")
        return

    print(f"检出 {len(results)} 个候选，新鲜 {len(fresh)} 个")
    for r in fresh[:ALERT_N]:
        print(f"  {r['score']:>3}分 {r['icon']} {r['symbol']:<15} {r['kind']:<8} OI{r['oi_change']:+.2f}% 价{r['price_change']:+.2f}% 量{r['vol_spike']:.1f}x")

    if FEISHU_WEBHOOK:
        try:
            send_card(build_card(fresh))
            print("✓ 飞书推送成功")
        except Exception as e:
            print(f"飞书推送失败: {e}")


if __name__ == "__main__":
    main()
