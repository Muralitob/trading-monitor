"""
通道信号回测（近 30 天）
用同一套 detect_channel + check_channel 逻辑跑历史，模拟成交，输出指标。

交易规则（贴合当前 briefing）：
- 触碰下沿 = 做多 / 触碰上沿 = 做空
- 入场 = 触发那根 2H K线的收盘价
- 止损 = 用 _build_trade_meta 里的规则（通道另一侧或最近极值+缓冲）
- T1 目标 = 1.5R（平 50%，剩余止损挪 BE）
- T2 目标 = 3R（平剩 50%）
- 未来 N 根 2H K线内没触发止盈也没扫止损 → 按最后一根收盘价平仓（超时）
"""
import sys, os, json, urllib.request, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TG_TOKEN", "x")
os.environ.setdefault("TG_CHAT", "x")

from monitor import klines, detect_channel

SYMBOLS = ["SOXLUSDT", "KORUUSDT"]
DAYS_BACK = 30
BARS_2H_PER_DAY = 12
LOOKBACK_BARS = 200          # 通道识别用的历史长度
MAX_HOLD_BARS = 40           # 单笔最多持仓 40 根 2H (≈ 6.7 天)
TOL_PCT = 0.05               # 触碰容差 (通道宽度 5%)

def compute_stop_target(direction, ch, k2h_up_to_now, current):
    """复刻 monitor.py 里 _build_trade_meta 的止损止盈逻辑"""
    highs_d = [float(x[2]) for x in k2h_up_to_now]
    lows_d = [float(x[3]) for x in k2h_up_to_now]
    upper = ch["upper"]; lower = ch["lower"]
    if direction == "SHORT":
        recent_high = max(highs_d[-20:])
        stop = max(recent_high * 1.003, upper * 1.008)
        target = lower
    else:
        recent_low = min(lows_d[-20:])
        stop = min(recent_low * 0.997, lower * 0.992)
        target = upper
    return stop, target


def simulate_trade(direction, entry, stop, target_2r, future_klines):
    """
    模拟 T1(1.5R) + T2(3R) 分批止盈：
    - 到 T1 → 半仓退出 +1.5R，止损挪到 BE
    - 剩下半仓等 T2 or BE 扫回
    - MAX_HOLD_BARS 后未触发任何目标 → 收盘价平仓
    返回：total_r（R 倍数）, outcome
    """
    r = abs(entry - stop)
    if r == 0:
        return 0, "invalid"

    t1 = entry + 1.5 * r if direction == "LONG" else entry - 1.5 * r
    t2 = entry + 3.0 * r if direction == "LONG" else entry - 3.0 * r
    breakeven = entry
    hit_t1 = False
    current_stop = stop

    for k in future_klines[:MAX_HOLD_BARS]:
        h = float(k[2]); l = float(k[3])
        if direction == "LONG":
            # 先看止损
            if l <= current_stop:
                # 已 T1 = 半仓 +1.5R，另半仓 BE 归零，总 +0.75R
                # 未 T1 = 全仓 -1R
                return (0.75 if hit_t1 else -1.0), ("t1_then_be" if hit_t1 else "stop")
            # 再看止盈
            if h >= t2:
                # 半仓 +1.5R + 半仓 +3R = +2.25R
                if hit_t1:
                    return 2.25, "t1_and_t2"
                else:
                    # 一根 K线同时穿过 T1 和 T2，保守视为 T1 触发
                    hit_t1 = True
                    current_stop = breakeven
                    return 2.25, "t1_and_t2"
            if not hit_t1 and h >= t1:
                hit_t1 = True
                current_stop = breakeven
        else:  # SHORT
            if h >= current_stop:
                return (0.75 if hit_t1 else -1.0), ("t1_then_be" if hit_t1 else "stop")
            if l <= t2:
                if hit_t1:
                    return 2.25, "t1_and_t2"
                else:
                    hit_t1 = True
                    current_stop = breakeven
                    return 2.25, "t1_and_t2"
            if not hit_t1 and l <= t1:
                hit_t1 = True
                current_stop = breakeven

    # 超时平仓
    last_close = float(future_klines[min(MAX_HOLD_BARS, len(future_klines))-1][4])
    if direction == "LONG":
        pnl = last_close - entry
    else:
        pnl = entry - last_close
    r_mult = pnl / r
    # 已 T1 的话，前半仓 +0.75R (半仓的 1.5R)，后半仓按当前 R
    if hit_t1:
        return (0.75 + r_mult * 0.5), "timeout_after_t1"
    return r_mult, "timeout"


def backtest_symbol(symbol):
    total_bars_needed = DAYS_BACK * BARS_2H_PER_DAY + LOOKBACK_BARS + MAX_HOLD_BARS
    total_bars_needed = min(total_bars_needed, 1500)
    print(f"\n=== {symbol} 回测中（拉 {total_bars_needed} 根 2H K线）===")
    all_k = klines(symbol, "2h", total_bars_needed)
    if len(all_k) < LOOKBACK_BARS + 10:
        print(f"数据不足: {len(all_k)}")
        return []

    trades = []
    # 从 LOOKBACK_BARS 开始滑动到倒数 MAX_HOLD_BARS
    start = LOOKBACK_BARS
    end = len(all_k) - 5  # 留 5 根 K线做未来
    # 只算过去 30 天窗口内的触发
    window_start_ts = (datetime.datetime.utcnow() - datetime.timedelta(days=DAYS_BACK)).timestamp() * 1000

    # 每个信号一天只触发 1 次（同 state.json 去重）
    fired_upper_days = set()
    fired_lower_days = set()

    for t in range(start, end):
        bar_ts = int(all_k[t][0])
        if bar_ts < window_start_ts:
            continue

        # 用 t 之前的 LOOKBACK_BARS 检测通道
        window = all_k[max(0, t - LOOKBACK_BARS):t + 1]
        ch = detect_channel(window)
        if not ch:
            continue

        prev_c = float(all_k[t-1][4])
        curr_c = float(all_k[t][4])
        upper = ch["upper"]; lower = ch["lower"]
        tol = (upper - lower) * TOL_PCT

        bar_date = datetime.datetime.utcfromtimestamp(bar_ts / 1000).strftime("%Y-%m-%d")

        # 检测触碰
        direction = None
        touch_type = None
        if lower - tol <= curr_c <= lower + tol and prev_c > lower + tol:
            if bar_date not in fired_lower_days:
                fired_lower_days.add(bar_date)
                direction = "LONG" if ch["direction"] == "上升通道" else "LONG"  # 触下沿都做多
                touch_type = "触碰下沿"
        elif upper - tol <= curr_c <= upper + tol and prev_c < upper - tol:
            if bar_date not in fired_upper_days:
                fired_upper_days.add(bar_date)
                direction = "SHORT"
                touch_type = "触碰上沿"

        if not direction:
            continue

        entry = curr_c
        stop, target = compute_stop_target(direction, ch, window, entry)
        future = all_k[t+1:]
        r_result, outcome = simulate_trade(direction, entry, stop, target, future)

        trades.append({
            "date": bar_date,
            "type": touch_type,
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "r_result": r_result,
            "outcome": outcome,
            "channel_dir": ch["direction"],
        })

    return trades


def print_report(symbol, trades):
    if not trades:
        print(f"{symbol}: 30 天内无触发信号")
        return

    n = len(trades)
    wins = sum(1 for t in trades if t["r_result"] > 0)
    losses = sum(1 for t in trades if t["r_result"] < 0)
    breakeven_ = n - wins - losses
    total_r = sum(t["r_result"] for t in trades)
    avg_r = total_r / n
    win_rate = wins / n * 100

    print(f"\n{'='*60}")
    print(f"{symbol} · 30天回测结果")
    print(f"{'='*60}")
    print(f"总交易数: {n}")
    print(f"胜率:     {win_rate:.1f}%  ({wins}W / {losses}L / {breakeven_}保本)")
    print(f"总 R:     {total_r:+.2f}R")
    print(f"平均 R:   {avg_r:+.2f}R / 单")
    print(f"若每单风险 $30 → 30天总盈亏: ${total_r * 30:+.0f}")
    print(f"若每单风险 $50 → 30天总盈亏: ${total_r * 50:+.0f}")
    print()

    # 逐笔明细
    print(f"{'日期':<12}{'方向':<7}{'类型':<10}{'入场':>10}{'止损':>10}{'目标':>10}  {'R':>7}  结局")
    print("-" * 90)
    for t in trades:
        print(f"{t['date']:<12}{t['direction']:<7}{t['type']:<10}"
              f"{t['entry']:>10.4g}{t['stop']:>10.4g}{t['target']:>10.4g}  "
              f"{t['r_result']:>+7.2f}  {t['outcome']}")


def main():
    all_trades = {}
    for sym in SYMBOLS:
        trades = backtest_symbol(sym)
        all_trades[sym] = trades
        print_report(sym, trades)

    # 总汇总
    total_all = [t for trades in all_trades.values() for t in trades]
    if total_all:
        n = len(total_all)
        wins = sum(1 for t in total_all if t["r_result"] > 0)
        total_r = sum(t["r_result"] for t in total_all)
        print(f"\n{'='*60}")
        print(f"总汇总（HYPE + MU）")
        print(f"{'='*60}")
        print(f"总交易数: {n}")
        print(f"胜率:     {wins/n*100:.1f}%")
        print(f"总 R:     {total_r:+.2f}R")
        print(f"每单 $30 → 总盈亏: ${total_r * 30:+.0f}")


if __name__ == "__main__":
    main()
