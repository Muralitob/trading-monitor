"""
图表生成模块
用 matplotlib 画 K 线 + 通道 / EMA / 关键位标注
"""
import os
import tempfile
import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import datetime

# 深色主题，接近 TradingView
BG = "#131722"
GRID = "#1e222d"
GREEN = "#26a69a"
RED = "#ef5350"
TEXT = "#d1d4dc"
BLUE = "#4A90E2"
YELLOW = "#f5c542"
ORANGE = "#ff9800"

# 全套 EMA 配色（画在所有图上）
EMA_STYLES = [
    (12,  "#9E9E9E", "EMA12",  0.7, 0.9),   # 灰
    (21,  "#f5c542", "EMA21",  0.9, 1.0),   # 黄
    (52,  "#4A90E2", "EMA52",  1.0, 1.1),   # 蓝
    (100, "#ff9800", "EMA100", 1.0, 1.2),   # 橙
    (200, "#e91e63", "EMA200", 1.0, 1.4),   # 粉红
]


def _ema(values, period):
    if not values or len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _overlay_all_emas(ax, klines_data, highlight_name=None):
    """在给定 ax 上叠画全套 EMA。highlight_name 会加粗突出显示。"""
    closes = [float(k[4]) for k in klines_data]
    n = len(closes)
    xs = list(range(n))
    for period, color, name, alpha, lw in EMA_STYLES:
        if n < period + 1:
            continue
        series = _ema(closes, period)
        line_w = lw + 0.6 if name == highlight_name else lw
        line_alpha = 1.0 if name == highlight_name else alpha
        ax.plot(xs, series, color=color, linewidth=line_w, alpha=line_alpha, label=name)
        # 右端标签
        ax.annotate(f"{name} {series[-1]:.4g}", xy=(n - 1, series[-1]),
                    xytext=(5, 0), textcoords="offset points",
                    color=color, fontsize=7.5,
                    fontweight="bold" if name == highlight_name else "normal")


def _style(ax):
    ax.set_facecolor(BG)
    ax.tick_params(colors=TEXT, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color(GRID)


def _draw_candles(ax, klines_data, offset=0):
    """画蜡烛图。klines_data 是 [[t,o,h,l,c,v,...],...]"""
    for i, k in enumerate(klines_data):
        o = float(k[1]); h = float(k[2]); l = float(k[3]); c = float(k[4])
        x = i + offset
        color = GREEN if c >= o else RED
        ax.plot([x, x], [l, h], color=color, linewidth=0.9)
        bh = abs(c - o)
        if bh < (h - l) * 0.005:
            bh = (h - l) * 0.005  # 十字星最小可见高度
        ax.add_patch(Rectangle(
            (x - 0.35, min(o, c)), 0.7, bh,
            facecolor=color, edgecolor=color, linewidth=0
        ))


def chart_channel(symbol, ch, klines_2h, out_dir=None, timeframe="2H"):
    """
    绘制 2H 通道图
    ch: detect_channel 返回的 dict，包含 direction/upper/lower/slope_upper/slope_lower
    返回 png 文件路径
    """
    n = len(klines_2h)
    fig, ax = plt.subplots(figsize=(11, 6), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)

    _draw_candles(ax, klines_2h)
    _overlay_all_emas(ax, klines_2h)

    # 通道线：反推 intercept，画出上下沿及延伸
    us = ch["slope_upper"]
    ls = ch["slope_lower"]
    cur_idx = n - 1
    # upper_now = us * cur_idx + up_int  →  up_int = upper_now - us * cur_idx
    up_int = ch["upper"] - us * cur_idx
    lo_int = ch["lower"] - ls * cur_idx

    xs = list(range(-2, n + 3))  # 向左右各延伸几根
    upper_line = [us * x + up_int for x in xs]
    lower_line = [ls * x + lo_int for x in xs]
    ax.plot(xs, upper_line, color=BLUE, linewidth=1.4, linestyle="-", alpha=0.9)
    ax.plot(xs, lower_line, color=BLUE, linewidth=1.4, linestyle="-", alpha=0.9)

    # 当前价横线
    cur_price = ch["current"]
    ax.axhline(y=cur_price, color=YELLOW, linewidth=0.8, linestyle="--", alpha=0.6)

    # 标注（全英文避免字体依赖）
    ax.annotate(f"UP {ch['upper']:.4g}", xy=(n - 1, ch["upper"]),
                xytext=(5, 5), textcoords="offset points",
                color=BLUE, fontsize=9)
    ax.annotate(f"DN {ch['lower']:.4g}", xy=(n - 1, ch["lower"]),
                xytext=(5, -12), textcoords="offset points",
                color=BLUE, fontsize=9)
    ax.annotate(f"NOW {cur_price:.4g}", xy=(n - 1, cur_price),
                xytext=(5, -4), textcoords="offset points",
                color=YELLOW, fontsize=9, fontweight="bold")

    # 标题
    dir_en = {"下降通道":"FALLING CHANNEL","上升通道":"RISING CHANNEL","水平通道":"FLAT CHANNEL"}.get(ch["direction"], ch["direction"])
    title = f"{symbol}  {timeframe}  {dir_en}   touch {ch['touch_upper']}/{ch['touch_lower']}  R^2 {ch['r2_upper']:.2f}/{ch['r2_lower']:.2f}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    # X 轴用时间
    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in klines_2h]
    step = max(1, n // 8)
    xticks = list(range(0, n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in xticks], rotation=0)

    ax.set_xlim(-1, n + 8)
    ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=7, ncol=5)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_channel_{symbol}_{timeframe}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path


def chart_ema_touch(symbol, klines_4h, ema25, ema100, close_price, ema_name, ema_val, out_dir=None, timeframe="4H"):
    """绘制 EMA 触碰/突破图（叠画全套 EMA）"""
    n = len(klines_4h)
    show_n = min(120, n)  # 画最近 120 根，让 EMA200 也能有值
    kdata = klines_4h[-show_n:]

    fig, ax = plt.subplots(figsize=(11, 6), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)
    _draw_candles(ax, kdata)
    _overlay_all_emas(ax, kdata, highlight_name=ema_name)

    # 高亮触碰的 EMA
    highlight_color = next((c for p, c, n_, a, l in EMA_STYLES if n_ == ema_name), YELLOW)
    ax.axhline(y=ema_val, color=highlight_color, linewidth=0.7, linestyle=":", alpha=0.6)
    ax.annotate(f"close {close_price:.4g}", xy=(show_n - 1, close_price),
                xytext=(5, -12), textcoords="offset points",
                color=TEXT, fontsize=9, fontweight="bold")

    title = f"{symbol}  {timeframe}  touching {ema_name}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in kdata]
    step = max(1, show_n // 8)
    xticks = list(range(0, show_n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in xticks])
    ax.set_xlim(-1, show_n + 8)

    ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=7, ncol=5)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_ema_{symbol}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path


def chart_pattern(symbol, pattern, klines_4h, out_dir=None):
    """绘制形态突破图（三角/楔形/矩形）"""
    show_n = min(100, len(klines_4h))
    kdata = klines_4h[-show_n:]
    n = len(kdata)
    offset = len(klines_4h) - n  # 原始 index 到新 index 的偏移

    fig, ax = plt.subplots(figsize=(11, 6), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)
    _draw_candles(ax, kdata)
    _overlay_all_emas(ax, kdata)

    # 画形态上下沿（用原始拟合线，转到新 index 上）
    us = pattern["slope_upper"]; ui = pattern["up_int"]
    ls = pattern["slope_lower"]; li = pattern["lo_int"]
    # 原坐标 idx_orig → 新坐标 idx_new = idx_orig - offset
    xs = list(range(0, n + 3))
    upper_line = [us * (x + offset) + ui for x in xs]
    lower_line = [ls * (x + offset) + li for x in xs]

    # 只画形态存在的那段（更清晰）
    start_new = max(0, pattern["start_idx"] - offset)
    end_new = min(n - 1, max(p[0] for p in pattern["upper_pts"] + pattern["lower_pts"]) - offset)
    seg_xs = list(range(start_new, end_new + 8))
    seg_upper = [us * (x + offset) + ui for x in seg_xs]
    seg_lower = [ls * (x + offset) + li for x in seg_xs]
    ax.plot(seg_xs, seg_upper, color=BLUE, linewidth=1.6, alpha=0.9)
    ax.plot(seg_xs, seg_lower, color=BLUE, linewidth=1.6, alpha=0.9)

    # 标注 swing 触点
    for idx, price, _kind in pattern["upper_pts"]:
        new_x = idx - offset
        if 0 <= new_x < n:
            ax.plot(new_x, price, "o", color=RED, markersize=5)
    for idx, price, _kind in pattern["lower_pts"]:
        new_x = idx - offset
        if 0 <= new_x < n:
            ax.plot(new_x, price, "o", color=GREEN, markersize=5)

    # 标签
    ax.annotate(f"UP {pattern['upper']:.4g}", xy=(n - 1, pattern["upper"]),
                xytext=(5, 5), textcoords="offset points", color=BLUE, fontsize=9)
    ax.annotate(f"DN {pattern['lower']:.4g}", xy=(n - 1, pattern["lower"]),
                xytext=(5, -12), textcoords="offset points", color=BLUE, fontsize=9)

    title = f"{symbol}  4H  {pattern['name_en']}   R^2 {pattern['r2_upper']:.2f}/{pattern['r2_lower']:.2f}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in kdata]
    step = max(1, n // 8)
    xticks = list(range(0, n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in xticks])
    ax.set_xlim(-1, n + 8)
    ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=7, ncol=5)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_pattern_{symbol}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path


def chart_level(symbol, klines_data, level, current_price, desc="", out_dir=None):
    """绘制关键位穿越 / 破位反抽 图（用 5m 数据）"""
    n = len(klines_data)
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)
    _draw_candles(ax, klines_data)

    ax.axhline(y=level, color=BLUE, linewidth=1.6, linestyle="-", alpha=0.9)
    ax.annotate(f"level {level:.4g}", xy=(n - 1, level),
                xytext=(5, 5), textcoords="offset points",
                color=BLUE, fontsize=10, fontweight="bold")
    ax.annotate(f"now {current_price:.4g}", xy=(n - 1, current_price),
                xytext=(5, -10), textcoords="offset points",
                color=YELLOW, fontsize=9)

    desc_en = {"反抽阻力":"RETEST RESISTANCE","回踩支撑":"RETEST SUPPORT","触碰下沿":"TOUCH LOWER","触碰上沿":"TOUCH UPPER"}.get(desc, desc)
    title = f"{symbol}  5m  {desc_en}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in klines_data]
    step = max(1, n // 8)
    xticks = list(range(0, n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%H:%M") for i in xticks])
    ax.set_xlim(-1, n + 3)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_level_{symbol}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path
