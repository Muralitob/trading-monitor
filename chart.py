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


def chart_channel(symbol, ch, klines_2h, out_dir=None):
    """
    绘制 2H 通道图
    ch: detect_channel 返回的 dict，包含 direction/upper/lower/slope_upper/slope_lower
    返回 png 文件路径
    """
    n = len(klines_2h)
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)

    _draw_candles(ax, klines_2h)

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
    title = f"{symbol}  2H  {dir_en}   touch {ch['touch_upper']}/{ch['touch_lower']}  R^2 {ch['r2_upper']:.2f}/{ch['r2_lower']:.2f}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    # X 轴用时间
    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in klines_2h]
    step = max(1, n // 8)
    xticks = list(range(0, n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in xticks], rotation=0)

    ax.set_xlim(-1, n + 3)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_channel_{symbol}_{int(datetime.datetime.utcnow().timestamp())}.png")
    plt.savefig(out_path, facecolor=BG, edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return out_path


def chart_ema_touch(symbol, klines_4h, ema25, ema100, close_price, ema_name, ema_val, out_dir=None):
    """绘制 4H EMA 触碰图"""
    n = len(klines_4h)
    show_n = min(80, n)  # 只画最近 80 根
    kdata = klines_4h[-show_n:]
    e25 = ema25[-show_n:]
    e100 = ema100[-show_n:]

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=90)
    fig.patch.set_facecolor(BG)
    _style(ax)
    _draw_candles(ax, kdata)

    xs = list(range(show_n))
    ax.plot(xs, e25, color=YELLOW, linewidth=1.2, label="EMA25", alpha=0.9)
    ax.plot(xs, e100, color=ORANGE, linewidth=1.4, label="EMA100", alpha=0.9)

    # 高亮触碰点
    highlight_color = YELLOW if ema_name == "EMA25" else ORANGE
    ax.axhline(y=ema_val, color=highlight_color, linewidth=0.6, linestyle=":", alpha=0.5)
    ax.annotate(f"{ema_name} {ema_val:.4g}", xy=(show_n - 1, ema_val),
                xytext=(5, 5), textcoords="offset points",
                color=highlight_color, fontsize=9, fontweight="bold")
    ax.annotate(f"close {close_price:.4g}", xy=(show_n - 1, close_price),
                xytext=(5, -10), textcoords="offset points",
                color=TEXT, fontsize=9)

    title = f"{symbol}  4H  touching {ema_name}"
    ax.set_title(title, color=TEXT, fontsize=10, loc="left")

    times = [datetime.datetime.utcfromtimestamp(int(k[0]) / 1000) for k in kdata]
    step = max(1, show_n // 8)
    xticks = list(range(0, show_n, step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([times[i].strftime("%m-%d %H:%M") for i in xticks])
    ax.set_xlim(-1, show_n + 3)

    ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=8)
    plt.tight_layout()

    out_dir = out_dir or tempfile.gettempdir()
    out_path = os.path.join(out_dir, f"chart_ema_{symbol}_{int(datetime.datetime.utcnow().timestamp())}.png")
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
