"""
EMA12 顺势回踩 + Pinbar 确认 · 4H 回测

核心逻辑（贴合你的思路）：
1. 用 4H EMA50 定趋势方向（简单：EMA50 上升=UP，下降=DOWN）
2. 等 4H K 线影线触碰 EMA12（回踩发生）
3. **加 pinbar / rejection 确认**：
   - SHORT: 上影线 ≥ 实体 × 1.5 且高点 ≥ EMA12
   - LONG:  下影线 ≥ 实体 × 1.5 且低点 ≤ EMA12
4. 顺势入场 + 分批止盈
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TG_TOKEN", "x")
os.environ.setdefault("TG_CHAT", "x")
from monitor import klines, ema_series

SYMBOLS = ["SKHYNIXUSDT", "SNDKUSDT", "MUUSDT", "EWYUSDT",
           "SOXLUSDT", "HYPEUSDT", "BTCUSDT", "ETHUSDT", "XAUUSDT"]
DAYS_BACK = 30
EMA_PERIOD = 12
TREND_EMA = 50
TREND_SLOPE_LOOKBACK = 5
MAX_HOLD_BARS = 20
WICK_TO_BODY_RATIO = 1.5   # pinbar 影线/实体 门槛
MAX_STOP_PCT = 0.035        # 止损最大 3.5%


def simulate_trade(direction, entry, stop, future_klines):
    r = abs(entry - stop)
    if r == 0: return 0, "invalid"
    t1 = entry + 1.5 * r if direction == "LONG" else entry - 1.5 * r
    t2 = entry + 3.0 * r if direction == "LONG" else entry - 3.0 * r
    be = entry; hit_t1 = False; cs = stop
    for k in future_klines[:MAX_HOLD_BARS]:
        h = float(k[2]); l = float(k[3])
        if direction == "LONG":
            if l <= cs: return (0.75 if hit_t1 else -1.0), ("t1_be" if hit_t1 else "stop")
            if h >= t2: return 2.25, "t1_t2"
            if not hit_t1 and h >= t1: hit_t1 = True; cs = be
        else:
            if h >= cs: return (0.75 if hit_t1 else -1.0), ("t1_be" if hit_t1 else "stop")
            if l <= t2: return 2.25, "t1_t2"
            if not hit_t1 and l <= t1: hit_t1 = True; cs = be
    last_close = float(future_klines[min(MAX_HOLD_BARS, len(future_klines)) - 1][4])
    pnl = (last_close - entry) if direction == "LONG" else (entry - last_close)
    rm = pnl / r
    return (0.75 + rm * 0.5, "timeout_after_t1") if hit_t1 else (rm, "timeout")


def is_bearish_pinbar(o, h, l, c, ema):
    """上影线 rejection：K 线冲高触碰 EMA 后回落"""
    upper_wick = h - max(o, c)
    body = abs(c - o)
    if body == 0: body = 0.0001
    return upper_wick >= body * WICK_TO_BODY_RATIO and h >= ema


def is_bullish_pinbar(o, h, l, c, ema):
    """下影线 rejection：K 线下探触碰 EMA 后拉回"""
    lower_wick = min(o, c) - l
    body = abs(c - o)
    if body == 0: body = 0.0001
    return lower_wick >= body * WICK_TO_BODY_RATIO and l <= ema


def backtest(symbol):
    all_k = klines(symbol, "4h", 500)
    if len(all_k) < TREND_EMA + 10:
        return []
    closes = [float(k[4]) for k in all_k]
    ema12 = ema_series(closes, EMA_PERIOD)
    ema50 = ema_series(closes, TREND_EMA)

    window_start_ts = (datetime.datetime.utcnow() - datetime.timedelta(days=DAYS_BACK)).timestamp() * 1000
    trades = []

    for t in range(TREND_EMA + TREND_SLOPE_LOOKBACK, len(all_k) - 3):
        bar_ts = int(all_k[t][0])
        if bar_ts < window_start_ts:
            continue

        k = all_k[t]
        o = float(k[1]); h = float(k[2]); l = float(k[3]); c = float(k[4])
        e12 = ema12[t]
        e50 = ema50[t]
        e50_past = ema50[t - TREND_SLOPE_LOOKBACK]

        # 趋势方向：EMA50 斜率
        if e50 > e50_past * 1.001:
            trend = "UP"
        elif e50 < e50_past * 0.999:
            trend = "DOWN"
        else:
            continue

        # 顺势 + pinbar 确认
        direction = None
        if trend == "DOWN" and is_bearish_pinbar(o, h, l, c, e12):
            direction = "SHORT"
            stop = h * 1.003
        elif trend == "UP" and is_bullish_pinbar(o, h, l, c, e12):
            direction = "LONG"
            stop = l * 0.997
        else:
            continue

        entry = c
        if abs(entry - stop) / entry > MAX_STOP_PCT:
            continue

        r_result, outcome = simulate_trade(direction, entry, stop, all_k[t + 1:])
        trades.append({
            "date": datetime.datetime.utcfromtimestamp(bar_ts / 1000).strftime("%m-%d %H:%M"),
            "trend": trend, "direction": direction,
            "entry": entry, "stop": stop, "ema": e12,
            "r_result": r_result, "outcome": outcome,
        })
    return trades


def print_report(sym, trades):
    if not trades:
        print(f"{sym:<14}  无触发")
        return None
    n = len(trades)
    wins = sum(1 for t in trades if t["r_result"] > 0)
    total_r = sum(t["r_result"] for t in trades)
    print(f"{sym:<14}  {n:>2}单  胜率 {wins/n*100:>5.1f}%  R {total_r:>+6.2f}  $30={total_r*30:>+5.0f}  $50={total_r*50:>+5.0f}")
    return {"n": n, "wins": wins, "total_r": total_r, "trades": trades}


def main():
    print(f"\n{'='*80}")
    print(f"EMA12 顺势回踩 + Pinbar 确认 · {DAYS_BACK} 天回测")
    print(f"{'='*80}\n")
    aggs = []
    all_t = []
    for sym in SYMBOLS:
        trades = backtest(sym)
        agg = print_report(sym, trades)
        if agg:
            aggs.append((sym, agg))
            all_t.extend(trades)

    print(f"\n{'='*80}")
    print("总汇总")
    print(f"{'='*80}")
    if aggs:
        tn = sum(a["n"] for _, a in aggs)
        tw = sum(a["wins"] for _, a in aggs)
        tr = sum(a["total_r"] for _, a in aggs)
        print(f"{tn} 单  胜率 {tw/tn*100:.1f}%  总 R {tr:+.2f}")
        print(f"每单 $30 → 30天盈亏: ${tr*30:+.0f}")
        print(f"每单 $50 → 30天盈亏: ${tr*50:+.0f}")

        # 打印所有信号
        if all_t:
            print(f"\n=== 所有交易明细 ===")
            print(f"{'品种':<14}{'日期':<16}{'趋势':<6}{'方向':<7}{'入场':>10}{'止损':>10}{'EMA':>10}  {'R':>7}  结局")
            # 按日期排序，附品种
            trades_with_sym = []
            for sym, agg in aggs:
                for t in agg["trades"]:
                    trades_with_sym.append((sym, t))
            trades_with_sym.sort(key=lambda x: x[1]["date"])
            for sym, t in trades_with_sym:
                print(f"{sym:<14}{t['date']:<16}{t['trend']:<6}{t['direction']:<7}{t['entry']:>10.4g}{t['stop']:>10.4g}{t['ema']:>10.4g}  {t['r_result']:>+7.2f}  {t['outcome']}")


if __name__ == "__main__":
    main()
