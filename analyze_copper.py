"""
铜（COPPERUSDT）单品种深度分析
用法：TG_TOKEN=x TG_CHAT=x python3 analyze_copper.py
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TG_TOKEN", "x")
os.environ.setdefault("TG_CHAT", "x")

from monitor import (
    klines, ema_series, detect_channel,
    _check_ema_rejection, _check_ema_break,
    FEISHU_WEBHOOK,
)
import json, urllib.request

SYM = "COPPERUSDT"
print(f"=" * 60)
print(f"COPPERUSDT 铜 · 深度分析")
print(f"=" * 60)

# 1. 24h ticker
ticker = json.loads(urllib.request.urlopen(
    f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={SYM}", timeout=10).read())
print(f"\n【当前状态】")
print(f"  现价:      ${float(ticker['lastPrice']):.4f}")
print(f"  24h 涨跌:  {float(ticker['priceChangePercent']):+.2f}%")
print(f"  24h 高:    ${float(ticker['highPrice']):.4f}")
print(f"  24h 低:    ${float(ticker['lowPrice']):.4f}")
print(f"  24h 成交:  ${float(ticker['quoteVolume'])/1e6:.1f}M")

# 2. 多周期 K线
k4h = klines(SYM, "4h", 250)
k1d = klines(SYM, "1d", 60)
k1h = klines(SYM, "1h", 100)
closes_4h = [float(k[4]) for k in k4h]
closes_1d = [float(k[4]) for k in k1d]
cur = closes_4h[-1]

# 3. 4H EMA 状态
print(f"\n【4H EMA 位置】")
for period, name in [(12, 'EMA12'), (21, 'EMA21'), (52, 'EMA52'), (100, 'EMA100'), (200, 'EMA200')]:
    if len(closes_4h) < period + 3:
        print(f"  {name:<8} 数据不足")
        continue
    ema = ema_series(closes_4h, period)[-1]
    dist = (cur - ema) / ema * 100
    side = "上方" if dist > 0 else "下方"
    icon = "🟢" if dist > 0 else "🔴"
    print(f"  {icon} {name:<8} ${ema:.4f}  距现价 {dist:+.2f}% ({side})")

# 4. 日线 EMA
print(f"\n【日线 EMA 位置】")
for period, name in [(12, 'EMA12'), (21, 'EMA21'), (52, 'EMA52'), (100, 'EMA100'), (200, 'EMA200')]:
    if len(closes_1d) < period + 3:
        continue
    ema = ema_series(closes_1d, period)[-1]
    dist = (closes_1d[-1] - ema) / ema * 100
    icon = "🟢" if dist > 0 else "🔴"
    print(f"  {icon} {name:<8} ${ema:.4f}  距 {dist:+.2f}%")

# 5. 关键价位
print(f"\n【关键位（近 20 日）】")
highs_1d = [float(k[2]) for k in k1d[-20:]]
lows_1d = [float(k[3]) for k in k1d[-20:]]
print(f"  20日高:  ${max(highs_1d):.4f}")
print(f"  20日低:  ${min(lows_1d):.4f}")
print(f"  20日均:  ${sum(closes_1d[-20:])/20:.4f}")

# 6. 通道检测
k2h = klines(SYM, "2h", 200)
ch = detect_channel(k2h)
if ch:
    width = ch['upper'] - ch['lower']
    pos = (cur - ch['lower']) / width * 100
    print(f"\n【2H 通道】")
    print(f"  方向:    {ch['direction']}")
    print(f"  上沿:    ${ch['upper']:.4f}")
    print(f"  下沿:    ${ch['lower']:.4f}")
    print(f"  位置:    {pos:.0f}% ({'接近下沿' if pos<25 else '接近上沿' if pos>75 else '中部'})")
    print(f"  触点:    上{ch['touch_upper']} / 下{ch['touch_lower']}")
    print(f"  R²:      {ch['r2_upper']:.2f} / {ch['r2_lower']:.2f}")
else:
    print(f"\n【2H 通道】未识别到有效通道（可能在震荡/趋势模糊阶段）")

# 7. 4H K线形态最近 5 根
print(f"\n【最近 5 根 4H K线】")
print(f"  {'时间':<16}{'O':>10}{'H':>10}{'L':>10}{'C':>10}{'变化':>8}")
for k in k4h[-5:]:
    ts = datetime.datetime.utcfromtimestamp(int(k[0])/1000) + datetime.timedelta(hours=8)
    o,h,l,c = float(k[1]),float(k[2]),float(k[3]),float(k[4])
    chg = (c-o)/o*100
    print(f"  {ts.strftime('%m-%d %H:%M')}{o:>10.4f}{h:>10.4f}{l:>10.4f}{c:>10.4f}  {chg:+.2f}%")

# 8. 拒绝/突破形态检测
print(f"\n【近 3 根 4H K线拒绝/突破形态】")
found = False
for period, name in [(21, 'EMA21'), (52, 'EMA52'), (100, 'EMA100'), (200, 'EMA200')]:
    if len(closes_4h) < period + 5: continue
    ema = ema_series(closes_4h, period)
    for i in range(-4, -1):
        bar = k4h[i]
        e_val = ema[i]
        e_prev = ema[i-1]
        rd, rm = _check_ema_rejection(bar, e_val, min_wick_ratio=1.0)
        if rd:
            ts = datetime.datetime.utcfromtimestamp(int(bar[0])/1000) + datetime.timedelta(hours=8)
            print(f"  🎯 {ts.strftime('%m-%d %H:%M')} {name} 拒绝 → {rd}  影/实={rm['wick_ratio']:.2f}")
            found = True
        bd, bm = _check_ema_break(bar, k4h[i-1], e_val, e_prev)
        if bd:
            ts = datetime.datetime.utcfromtimestamp(int(bar[0])/1000) + datetime.timedelta(hours=8)
            print(f"  🚀 {ts.strftime('%m-%d %H:%M')} {name} 突破 → {bd}  距 EMA {bm['dist_pct']:+.2f}%")
            found = True
if not found:
    print("  近 3 根 K 线无明显形态")

# 9. 综合评估
print(f"\n【综合判断】")
# 大趋势
ema50d = ema_series(closes_1d, 50)[-1] if len(closes_1d) >= 50 else None
if ema50d:
    trend_pct = (closes_1d[-1] - ema50d) / ema50d * 100
    if trend_pct > 3:
        bias = "🟢 多头强势"
    elif trend_pct > 0:
        bias = "🟡 多头弱势"
    elif trend_pct > -3:
        bias = "🟡 空头弱势"
    else:
        bias = "🔴 空头强势"
    print(f"  大趋势:  {bias}  (距日EMA50 {trend_pct:+.2f}%)")

# 短期
ema21_4h = ema_series(closes_4h, 21)[-1]
short_pct = (cur - ema21_4h) / ema21_4h * 100
if short_pct > 1:
    short_bias = "🟢 强于短均线"
elif short_pct > -1:
    short_bias = "🟡 贴近短均线"
else:
    short_bias = "🔴 弱于短均线"
print(f"  短期:    {short_bias}  (距 4H EMA21 {short_pct:+.2f}%)")

# 波动
change_24h = float(ticker['priceChangePercent'])
if abs(change_24h) < 1:
    vol_desc = "🟡 平静"
elif abs(change_24h) < 3:
    vol_desc = "🟢 正常"
else:
    vol_desc = "🔴 剧烈"
print(f"  波动:    {vol_desc}  (24h {change_24h:+.2f}%)")
