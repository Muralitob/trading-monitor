"""
震荡 / 趋势 行情识别（默认 4H，只用已收盘 K 线）

四个指标投票，每个指标说"震荡"记 1 票：
1. ADX(14) < 20                 —— 没有方向力度
2. Choppiness Index(14) > 55    —— 价格来回绕，走不远
3. 效率比 ER(20) < 0.25          —— 20 根走了很多路，净位移很少
4. EMA21 近 30 根被收盘价穿越 ≥ 4 次 —— 均线被反复穿，没有一边站稳

≥3 票 = 震荡 🟡；2 票 = 偏震荡/过渡 🟠；≤1 票 = 趋势 🟢

用法：
  python3 regime.py                   # 看 monitor 里全部品种
  python3 regime.py BTCUSDT SOLUSDT   # 指定品种
  python3 regime.py BTCUSDT --tf 1h
"""
import sys
import json
import urllib.request
import urllib.error

CHOP = "震荡"
LEAN_CHOP = "偏震荡"
TREND = "趋势"


def _klines(symbol, interval, limit):
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        return json.loads(urllib.request.urlopen(url, timeout=10).read())
    except urllib.error.HTTPError as e:
        if e.code != 451:
            raise
        # 本地 Mac 被合约接口地区限制时，退回现货公开数据（XAU/HYPE 等没有现货的会失败）
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        return json.loads(urllib.request.urlopen(url, timeout=10).read())


def _ema(values, period):
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return [None] * (period - 1) + out


def _true_ranges(highs, lows, closes):
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    return trs


def adx(highs, lows, closes, period=14):
    """Wilder ADX，返回最后一根的值"""
    trs = _true_ranges(highs, lows, closes)
    pdm, ndm = [0.0], [0.0]
    for i in range(1, len(closes)):
        up = highs[i] - highs[i-1]
        dn = lows[i-1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)

    def wilder(xs):
        s = sum(xs[1:period+1])
        out = [s]
        for x in xs[period+1:]:
            s = s - s / period + x
            out.append(s)
        return out

    tr_s, p_s, n_s = wilder(trs), wilder(pdm), wilder(ndm)
    dxs = []
    for t, p, n in zip(tr_s, p_s, n_s):
        if t == 0:
            dxs.append(0.0)
            continue
        pdi, ndi = 100 * p / t, 100 * n / t
        dxs.append(0.0 if pdi + ndi == 0 else 100 * abs(pdi - ndi) / (pdi + ndi))
    a = sum(dxs[:period]) / period
    for d in dxs[period:]:
        a = (a * (period - 1) + d) / period
    return a


def choppiness(highs, lows, closes, period=14):
    import math
    trs = _true_ranges(highs, lows, closes)[-period:]
    rng = max(highs[-period:]) - min(lows[-period:])
    if rng == 0:
        return 100.0
    return 100 * math.log10(sum(trs) / rng) / math.log10(period)


def efficiency_ratio(closes, period=20):
    net = abs(closes[-1] - closes[-1-period])
    path = sum(abs(closes[i] - closes[i-1]) for i in range(len(closes) - period, len(closes)))
    return net / path if path else 0.0


def ema_cross_count(closes, period=21, lookback=30):
    e = _ema(closes, period)
    sides = [1 if c > v else -1 for c, v in zip(closes[-lookback:], e[-lookback:])]
    return sum(1 for a, b in zip(sides, sides[1:]) if a != b)


def classify(k):
    """k：已收盘 K 线列表（币安格式，至少 60 根）。返回判断结果 dict"""
    highs = [float(x[2]) for x in k]
    lows = [float(x[3]) for x in k]
    closes = [float(x[4]) for x in k]

    v_adx = adx(highs, lows, closes)
    v_chop = choppiness(highs, lows, closes)
    v_er = efficiency_ratio(closes)
    v_cross = ema_cross_count(closes)

    votes = []
    if v_adx < 20:
        votes.append(f"ADX {v_adx:.0f} < 20（没方向力度）")
    if v_chop > 55:
        votes.append(f"CHOP {v_chop:.0f} > 55（来回绕）")
    if v_er < 0.25:
        votes.append(f"效率比 {v_er:.2f} < 0.25（走很多路、没净位移）")
    if v_cross >= 4:
        votes.append(f"EMA21 近30根被穿 {v_cross} 次")

    n = len(votes)
    regime = CHOP if n >= 3 else LEAN_CHOP if n == 2 else TREND

    # 震荡区间：近 30 根高低点，现价在区间里的位置（0=下沿，1=上沿）
    box_hi, box_lo = max(highs[-30:]), min(lows[-30:])
    cur = closes[-1]
    pos = (cur - box_lo) / (box_hi - box_lo) if box_hi > box_lo else 0.5

    e21, e52 = _ema(closes, 21)[-1], _ema(closes, 52)[-1]
    trend_dir = "多" if cur > e21 > e52 else "空" if cur < e21 < e52 else "混"

    return {
        "regime": regime, "votes": n, "reasons": votes,
        "adx": v_adx, "chop": v_chop, "er": v_er, "cross": v_cross,
        "box_hi": box_hi, "box_lo": box_lo, "pos": pos,
        "price": cur, "trend_dir": trend_dir,
    }


def chop_states(k, n=12):
    """最近 n 根的"是否处于震荡"序列，带滞后：≥3 票进入，≤1 票才退出，2 票维持原状态（防止来回跳）"""
    in_chop, states = False, []
    for end in range(len(k) - n + 1, len(k) + 1):
        votes = classify(k[:end])["votes"]
        if votes >= 3:
            in_chop = True
        elif votes <= 1:
            in_chop = False
        states.append(in_chop)
    return states


def get_regime(symbol, tf="4h"):
    k = _klines(symbol, tf, 151)[:-1]   # 去掉未收盘那根
    r = classify(k)
    r["symbol"], r["tf"], r["bar_close_ts"] = symbol, tf, k[-1][6] // 1000
    return r


def icon(regime):
    return {CHOP: "🟡", LEAN_CHOP: "🟠", TREND: "🟢"}[regime]


def pos_zh(pos):
    if pos >= 0.8:
        return "靠近上沿"
    if pos <= 0.2:
        return "靠近下沿"
    return "区间中部"


def warning_text(r, direction=None):
    """给推送消息用的震荡警告；趋势行情返回空字符串"""
    if r["regime"] == TREND:
        return ""
    tf = r["tf"].upper()
    lines = [f"{icon(r['regime'])} **⚠️ 注意：{tf} {r['regime']}行情**（{r['votes']}/4 票）"]
    for reason in r["reasons"]:
        lines.append(f"  · {reason}")
    lines.append(f"  区间 `${r['box_lo']:.4g}` – `${r['box_hi']:.4g}`，现价在{pos_zh(r['pos'])}（{r['pos']*100:.0f}%）")
    if 0.2 < r["pos"] < 0.8:
        lines.append("  👉 区间中部不开单：上下都是噪音，止损容易被来回扫")
    elif direction == "LONG" and r["pos"] >= 0.8:
        lines.append("  👉 震荡里在上沿追多 = 最常见的亏法，等真突破+回踩确认")
    elif direction == "SHORT" and r["pos"] <= 0.2:
        lines.append("  👉 震荡里在下沿追空 = 最常见的亏法，等真跌破+反抽确认")
    else:
        lines.append("  👉 震荡里边缘反向单回测也是亏的：要做就用 $30 最小档，或干脆等突破+回踩")
    return "\n".join(lines)


def _main(argv):
    tf = "4h"
    if "--tf" in argv:
        i = argv.index("--tf")
        tf = argv[i + 1]
        argv = argv[:i] + argv[i+2:]
    symbols = argv or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "HYPEUSDT", "XRPUSDT",
                       "ZECUSDT", "UNIUSDT", "AAVEUSDT", "XAUUSDT"]
    print(f"{'品种':<10} {'状态':<6} 票  ADX  CHOP   ER  穿EMA  均线  区间位置")
    for s in symbols:
        try:
            r = get_regime(s, tf)
        except Exception as e:
            print(f"{s:<10} 拉数据失败: {e}")
            continue
        print(f"{s:<10} {icon(r['regime'])}{r['regime']:<4} {r['votes']}  {r['adx']:4.0f}  {r['chop']:4.0f}  "
              f"{r['er']:.2f}  {r['cross']:>4}   {r['trend_dir']:<3}  {pos_zh(r['pos'])} {r['pos']*100:.0f}%")


if __name__ == "__main__":
    _main(sys.argv[1:])
