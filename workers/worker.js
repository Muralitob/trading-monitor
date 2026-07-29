/**
 * 交易监控 Worker
 * Cloudflare Workers Cron 每 5 分钟触发
 *
 * 修复：
 *  - 30 分钟滑动窗口（原 5min），漏报率大幅降低
 *  - KV 存储去重，同一信号 24h 内不重复
 *  - 直接从 Cloudflare 全球边缘触发，比 GH Actions 准时
 */

// ==================== 配置 ====================

const RULES = {
  HYPEUSDT: [
    { level: 58.0, dir: "up",   desc: "反弹到 58 阻力（做空关注区）" },
    { level: 60.0, dir: "up",   desc: "反弹到 60 关键阻力" },
    { level: 52.6, dir: "down", desc: "跌破 52.6 支撑（60日低点）" },
  ],
  MUUSDT: [
    { level: 885.0, dir: "up",   desc: "反弹到 885 阻力区（做空关注）" },
    { level: 900.0, dir: "up",   desc: "反弹到 900 关键阻力" },
    { level: 835.0, dir: "down", desc: "跌破 835 支撑" },
  ],
};

const ALL_SYMBOLS = [
  "BTCUSDT", "ETHUSDT",
  "HYPEUSDT",
  "XAUUSDT",
  "SOXLUSDT", "KORUUSDT",
  "EWYUSDT",
  "SKHYNIXUSDT", "SNDKUSDT",
  "MUUSDT",
];

const VOLATILITY_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAUUSDT"];
const VOL_THRESHOLD = 0.03;

// ==================== 工具函数 ====================

const FAPI_HOSTS = [
  "fapi1.binance.com",
  "fapi2.binance.com",
  "fapi3.binance.com",
  "fapi.binance.com",
];

async function klines(symbol, interval, limit) {
  let lastErr = null;
  for (const host of FAPI_HOSTS) {
    try {
      const url = `https://${host}/fapi/v1/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
      const resp = await fetch(url, {
        cf: { cacheTtl: 0 },
        headers: {
          "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
          "Accept": "application/json",
        },
      });
      if (!resp.ok) { lastErr = `HTTP ${resp.status}`; continue; }
      const text = await resp.text();
      try { return JSON.parse(text); }
      catch (e) { lastErr = `non-JSON (${text.slice(0, 60)})`; continue; }
    } catch (e) { lastErr = e.message; }
  }
  throw new Error(`klines ${symbol} ${interval}: all hosts failed, last: ${lastErr}`);
}

async function sendTG(env, text) {
  const url = `https://api.telegram.org/bot${env.TG_TOKEN}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: env.TG_CHAT,
      text: text,
      parse_mode: "Markdown",
    }),
  });
}

// KV 去重：key 存在 = 24h 内已推过
async function alreadyFired(env, key) {
  const v = await env.KV.get(key);
  return v !== null;
}
async function markFired(env, key) {
  await env.KV.put(key, String(Date.now()), { expirationTtl: 86400 });
}

function todayKey() {
  const d = new Date();
  return `${d.getUTCFullYear()}${(d.getUTCMonth()+1).toString().padStart(2,'0')}${d.getUTCDate().toString().padStart(2,'0')}`;
}

// ==================== EMA / Swing / Line ====================

function emaSeries(values, period) {
  if (!values.length) return [];
  const k = 2 / (period + 1);
  const out = [values[0]];
  for (let i = 1; i < values.length; i++) {
    out.push(values[i] * k + out[i-1] * (1 - k));
  }
  return out;
}

function findSwings(highs, lows, window = 3) {
  const sh = [], sl = [];
  for (let i = window; i < highs.length - window; i++) {
    let isHigh = true, isLow = true;
    for (let j = -window; j <= window; j++) {
      if (j === 0) continue;
      if (highs[i] < highs[i+j]) isHigh = false;
      if (lows[i] > lows[i+j]) isLow = false;
    }
    if (isHigh) sh.push([i, highs[i]]);
    if (isLow) sl.push([i, lows[i]]);
  }
  return { sh, sl };
}

function fitLine(points) {
  const n = points.length;
  if (n < 2) return null;
  const xs = points.map(p => p[0]);
  const ys = points.map(p => p[1]);
  const mx = xs.reduce((a,b)=>a+b,0) / n;
  const my = ys.reduce((a,b)=>a+b,0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i]-mx) * (ys[i]-my);
    den += (xs[i]-mx) ** 2;
  }
  if (den === 0) return null;
  const slope = num / den;
  const intercept = my - slope * mx;
  const ssTot = ys.reduce((s,y) => s + (y-my)**2, 0);
  const ssRes = ys.reduce((s,y,i) => s + (y - (slope*xs[i]+intercept))**2, 0);
  const r2 = ssTot > 0 ? 1 - ssRes/ssTot : 1.0;
  return { slope, intercept, r2 };
}

// ==================== 通道识别 ====================

function scoreChannel(upperPts, lowerPts, nBars, curPrice) {
  if (upperPts.length < 2 || lowerPts.length < 2) return null;
  const u = fitLine(upperPts);
  const l = fitLine(lowerPts);
  if (!u || !l) return null;
  const us = u.slope, ls = l.slope;

  let slopeDiff;
  if (Math.abs(us) < 1e-9 && Math.abs(ls) < 1e-9) slopeDiff = 0;
  else if (Math.abs(us) < 1e-9 || Math.abs(ls) < 1e-9) return null;
  else slopeDiff = Math.abs(us - ls) / Math.max(Math.abs(us), Math.abs(ls));
  if (slopeDiff > 0.3) return null;
  if (us * ls < 0 && Math.abs(us) > 1e-6 && Math.abs(ls) > 1e-6) return null;

  const curIdx = nBars - 1;
  const upperNow = us * curIdx + u.intercept;
  const lowerNow = ls * curIdx + l.intercept;
  if (upperNow <= lowerNow) return null;
  const width = upperNow - lowerNow;
  if (width < curPrice * 0.02 || width > curPrice * 0.5) return null;
  const position = (curPrice - lowerNow) / width;
  if (position < -0.2 || position > 1.2) return null;
  if (Math.max(u.r2, l.r2) < 0.85) return null;
  if (Math.min(u.r2, l.r2) < 0.4) return null;

  const score = u.r2 * l.r2 * (upperPts.length + lowerPts.length) / (1 + slopeDiff * 5);
  const direction = us < -1e-6 ? "下降通道" : (us > 1e-6 ? "上升通道" : "水平通道");
  return {
    score,
    ch: {
      direction, upper: upperNow, lower: lowerNow, current: curPrice,
      touchUpper: upperPts.length, touchLower: lowerPts.length,
      r2Upper: u.r2, r2Lower: l.r2,
    }
  };
}

function detectChannel(k2h) {
  if (k2h.length < 60) return null;
  const highs = k2h.map(k => parseFloat(k[2]));
  const lows = k2h.map(k => parseFloat(k[3]));
  const closes = k2h.map(k => parseFloat(k[4]));
  const n = k2h.length;
  const cur = closes[closes.length - 1];

  let best = null;
  for (const w of [3, 5, 7]) {
    const { sh, sl } = findSwings(highs, lows, w);
    for (const nu of [5, 4, 3, 2]) {
      for (const nl of [5, 4, 3, 2]) {
        if (sh.length < nu || sl.length < nl) continue;
        const result = scoreChannel(sh.slice(-nu), sl.slice(-nl), n, cur);
        if (!result) continue;
        if (!best || result.score > best.score) best = result;
      }
    }
  }
  return best ? best.ch : null;
}

// ==================== 关键位穿越（滑动窗口）====================

async function checkLevelCrossings(env, symbol, rules) {
  const alerts = [];
  const k5m = await klines(symbol, "5m", 8);
  if (k5m.length < 2) return alerts;

  const dateKey = todayKey();

  // 遍历最近 6 根 5m K线（包含最新未收盘），找"从上一根到当前根"的首次穿越
  // k[i-1] 是"上一根"（已收盘），k[i] 是"这一根"（也已收盘除了最新的）
  // 只看已收盘的 K线 → 从 k[1] 到 k[6]（跳过 k[7] 是当前未收盘）
  for (let i = 1; i < k5m.length - 1; i++) {
    const prevClose = parseFloat(k5m[i-1][4]);
    const currClose = parseFloat(k5m[i][4]);

    for (const r of rules) {
      const lv = r.level;
      let triggered = false, arrow = "";

      if (r.dir === "up" && prevClose < lv && currClose >= lv) {
        triggered = true;
        arrow = "⬆️";
      } else if (r.dir === "down" && prevClose > lv && currClose <= lv) {
        triggered = true;
        arrow = "⬇️";
      }

      if (triggered) {
        const dedupKey = `level:${symbol}:${lv}:${r.dir}:${dateKey}`;
        if (!(await alreadyFired(env, dedupKey))) {
          await markFired(env, dedupKey);
          const current = parseFloat(k5m[k5m.length - 1][4]);
          alerts.push({
            kind: "level", symbol, price: current, desc: r.desc, arrow,
          });
        }
      }
    }
  }
  return alerts;
}

// ==================== 通道触碰 ====================

async function checkChannel(env, symbol) {
  const k2h = await klines(symbol, "2h", 200);
  const ch = detectChannel(k2h);
  if (!ch) return null;

  const k5m = await klines(symbol, "5m", 6);
  const dateKey = todayKey();
  const tol = (ch.upper - ch.lower) * 0.05;

  for (let i = 1; i < k5m.length - 1; i++) {
    const prevClose = parseFloat(k5m[i-1][4]);
    const currClose = parseFloat(k5m[i][4]);

    // 触碰下沿
    if (ch.lower - tol <= currClose && currClose <= ch.lower + tol && prevClose > ch.lower + tol) {
      const key = `channel:${symbol}:lower:${dateKey}`;
      if (!(await alreadyFired(env, key))) {
        await markFired(env, key);
        const sig = ch.direction === "上升通道" ? "做多关注" : "反弹关注（**逆势，谨慎**）";
        return {
          symbol, type: "触碰下沿", channel: ch,
          price: parseFloat(k5m[k5m.length - 1][4]), signal: sig,
        };
      }
    }
    // 触碰上沿
    if (ch.upper - tol <= currClose && currClose <= ch.upper + tol && prevClose < ch.upper - tol) {
      const key = `channel:${symbol}:upper:${dateKey}`;
      if (!(await alreadyFired(env, key))) {
        await markFired(env, key);
        const sig = ch.direction === "下降通道" ? "做空关注" : "回落关注（**逆势，谨慎**）";
        return {
          symbol, type: "触碰上沿", channel: ch,
          price: parseFloat(k5m[k5m.length - 1][4]), signal: sig,
        };
      }
    }
  }
  return null;
}

// ==================== EMA 信号 ====================

function barJustClosed(kline, nowTs, tolSec = 480) {
  const closeTs = kline[6] / 1000;
  const age = nowTs - closeTs;
  return age >= 0 && age < tolSec;
}

async function checkEMASignals(env, symbol, nowTs) {
  const alerts = [];
  const dateKey = todayKey();

  // 4H EMA25 / EMA100
  try {
    const k4h = await klines(symbol, "4h", 150);
    if (k4h.length >= 105) {
      const closes = k4h.map(k => parseFloat(k[4]));
      const ema25 = emaSeries(closes, 25);
      const ema100 = emaSeries(closes, 100);
      const lastClosed = k4h[k4h.length - 2];

      if (barJustClosed(lastClosed, nowTs)) {
        const closeP = closes[closes.length - 2];
        const prevClose = closes[closes.length - 3];

        // EMA25 触碰
        const e25 = ema25[ema25.length - 2];
        const e25p = ema25[ema25.length - 3];
        const tol25 = e25 * 0.005;
        if (Math.abs(closeP - e25) <= tol25 && Math.abs(prevClose - e25p) > tol25) {
          const key = `ema25:${symbol}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            const side = closeP > e25 ? "上方" : "下方";
            alerts.push({
              symbol, text: `4H 收盘 ${side}触碰 EMA25`,
              detail: `现价 $${closeP.toFixed(4)}  EMA25 $${e25.toFixed(4)}`,
              icon: "📊",
            });
          }
        }

        // EMA100 触碰
        const e100 = ema100[ema100.length - 2];
        const e100p = ema100[ema100.length - 3];
        const tol100 = e100 * 0.008;
        if (Math.abs(closeP - e100) <= tol100 && Math.abs(prevClose - e100p) > tol100) {
          const key = `ema100:${symbol}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            const side = closeP > e100 ? "上方" : "下方";
            alerts.push({
              symbol, text: `🌟 4H 收盘 ${side}触碰 EMA100（大级别支撑）`,
              detail: `现价 $${closeP.toFixed(4)}  EMA100 $${e100.toFixed(4)}`,
              icon: "📊",
            });
          }
        }
      }
    }
  } catch (e) { console.log(`ema4h:${symbol} error: ${e}`); }

  // 日线 EMA50 穿越
  try {
    const k1d = await klines(symbol, "1d", 100);
    if (k1d.length >= 55) {
      const closesD = k1d.map(k => parseFloat(k[4]));
      const e50d = emaSeries(closesD, 50);
      const lastClosedD = k1d[k1d.length - 2];
      if (barJustClosed(lastClosedD, nowTs, 600)) {
        const closeD = closesD[closesD.length - 2];
        const prevD = closesD[closesD.length - 3];
        const e50 = e50d[e50d.length - 2];
        const e50p = e50d[e50d.length - 3];

        if (prevD < e50p && closeD >= e50) {
          const key = `ema50d_up:${symbol}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            alerts.push({
              symbol, text: "🌟🌟 日线收盘上穿 EMA50（大趋势转多）",
              detail: `收盘 $${closeD.toFixed(4)}  EMA50 $${e50.toFixed(4)}`,
              icon: "🚀",
            });
          }
        } else if (prevD > e50p && closeD <= e50) {
          const key = `ema50d_down:${symbol}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            alerts.push({
              symbol, text: "🌟🌟 日线收盘下穿 EMA50（大趋势转空）",
              detail: `收盘 $${closeD.toFixed(4)}  EMA50 $${e50.toFixed(4)}`,
              icon: "⚠️",
            });
          }
        }
      }
    }
  } catch (e) { console.log(`ema1d:${symbol} error: ${e}`); }

  return alerts;
}

// ==================== 破位反抽 ====================

async function checkRetest(env, symbol, rules) {
  const alerts = [];
  try {
    const k1h = await klines(symbol, "1h", 26);
    const k5m = await klines(symbol, "5m", 8);
    if (k1h.length < 3 || k5m.length < 3) return alerts;

    const dateKey = todayKey();

    for (const r of rules) {
      const lv = r.level;
      const tol = lv * 0.005;

      // 检查 24h 内是否破位
      let brokeDown = false, brokeUp = false;
      for (let i = 0; i < k1h.length - 1; i++) {
        const o = parseFloat(k1h[i][1]);
        const c = parseFloat(k1h[i][4]);
        if (o > lv && c < lv - tol) brokeDown = true;
        else if (o < lv && c > lv + tol) brokeUp = true;
      }

      // 反抽：滑动窗口检测
      for (let i = 1; i < k5m.length - 1; i++) {
        const prevClose = parseFloat(k5m[i-1][4]);
        const currClose = parseFloat(k5m[i][4]);

        if (brokeDown && prevClose < lv - tol && currClose >= lv - tol && currClose <= lv + tol) {
          const key = `retest_down:${symbol}:${lv}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            alerts.push({
              symbol, level: lv, type: "反抽阻力",
              text: `跌破 $${lv} 后反抽 → **做空关注**`,
              price: parseFloat(k5m[k5m.length - 1][4]), icon: "🔻",
            });
          }
        }

        if (brokeUp && prevClose > lv + tol && currClose >= lv - tol && currClose <= lv + tol) {
          const key = `retest_up:${symbol}:${lv}:${dateKey}`;
          if (!(await alreadyFired(env, key))) {
            await markFired(env, key);
            alerts.push({
              symbol, level: lv, type: "回踩支撑",
              text: `突破 $${lv} 后回踩 → **做多关注**`,
              price: parseFloat(k5m[k5m.length - 1][4]), icon: "🔺",
            });
          }
        }
      }
    }
  } catch (e) { console.log(`retest:${symbol} error: ${e}`); }
  return alerts;
}

// ==================== 大盘异动 ====================

async function checkVolatility(env, symbol, nowTs) {
  try {
    const k = await klines(symbol, "4h", 2);
    const lastClosed = k[k.length - 2];
    const openP = parseFloat(lastClosed[1]);
    const closeP = parseFloat(lastClosed[4]);
    const closeTs = lastClosed[6] / 1000;
    const age = nowTs - closeTs;
    if (age >= 0 && age < 480) {
      const change = (closeP - openP) / openP;
      if (Math.abs(change) >= VOL_THRESHOLD) {
        const dateKey = todayKey();
        const key = `vol:${symbol}:${dateKey}:${closeTs}`;
        if (!(await alreadyFired(env, key))) {
          await markFired(env, key);
          return {
            symbol, price: closeP,
            desc: `4H 振幅 ${(change*100).toFixed(2)}%`,
            arrow: change > 0 ? "📈" : "📉",
          };
        }
      }
    }
  } catch (e) { console.log(`vol:${symbol} error: ${e}`); }
  return null;
}

// ==================== 主入口 ====================

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runMonitor(env));
  },

  async fetch(request, env, ctx) {
    // 手动触发测试：访问 /run
    const url = new URL(request.url);
    if (url.pathname === "/run") {
      await runMonitor(env);
      return new Response("OK - monitor ran\n");
    }
    if (url.pathname === "/test") {
      await sendTG(env, "✅ Cloudflare Worker 通道测试成功");
      return new Response("Sent test message\n");
    }
    return new Response("trading-monitor worker\nEndpoints: /run /test\n");
  }
};

async function runMonitor(env) {
  const nowUtc = new Date();
  const nowTs = nowUtc.getTime() / 1000;
  const bjHour = (nowUtc.getUTCHours() + 8) % 24;

  if (bjHour >= 0 && bjHour < 7) {
    console.log(`Quiet hours (BJ ${bjHour}:xx), skip`);
    return;
  }

  const levelAlerts = [];
  const channelAlerts = [];
  const emaAlerts = [];
  const retestAlerts = [];
  const volAlerts = [];

  // 关键位穿越 + 反抽（只对配置过 RULES 的品种）
  for (const [symbol, rules] of Object.entries(RULES)) {
    levelAlerts.push(...(await checkLevelCrossings(env, symbol, rules)));
    retestAlerts.push(...(await checkRetest(env, symbol, rules)));
  }

  // 大盘异动
  for (const symbol of VOLATILITY_SYMBOLS) {
    const v = await checkVolatility(env, symbol, nowTs);
    if (v) volAlerts.push(v);
  }

  // 通道 + EMA（对所有品种）
  for (const symbol of ALL_SYMBOLS) {
    const c = await checkChannel(env, symbol);
    if (c) channelAlerts.push(c);
    emaAlerts.push(...(await checkEMASignals(env, symbol, nowTs)));
  }

  const total = levelAlerts.length + channelAlerts.length + emaAlerts.length +
                retestAlerts.length + volAlerts.length;

  if (total === 0) {
    console.log("No alerts triggered");
    return;
  }

  const lines = ["🔔 *交易信号*", ""];
  for (const a of levelAlerts) {
    lines.push(`${a.arrow} *${a.symbol}*  \`$${a.price}\``);
    lines.push(`  ${a.desc}`);
    lines.push("");
  }
  for (const a of volAlerts) {
    lines.push(`${a.arrow} *${a.symbol}*  \`$${a.price}\``);
    lines.push(`  ${a.desc}`);
    lines.push("");
  }
  for (const c of channelAlerts) {
    const ch = c.channel;
    const icon = c.type === "触碰下沿" ? "🔻" : "🔺";
    lines.push(`${icon} *${c.symbol}* 2H ${ch.direction} · ${c.type}`);
    lines.push(`  现价 \`$${c.price}\``);
    lines.push(`  上沿 \`$${ch.upper.toFixed(2)}\` / 下沿 \`$${ch.lower.toFixed(2)}\``);
    lines.push(`  触点 ${ch.touchUpper}/${ch.touchLower}  R² ${ch.r2Upper.toFixed(2)}/${ch.r2Lower.toFixed(2)}`);
    lines.push(`  ${c.signal}`);
    lines.push("");
  }
  for (const e of emaAlerts) {
    lines.push(`${e.icon} *${e.symbol}*  ${e.text}`);
    lines.push(`  ${e.detail}`);
    lines.push("");
  }
  for (const r of retestAlerts) {
    lines.push(`${r.icon} *${r.symbol}*  ${r.text}`);
    lines.push(`  现价 \`$${r.price}\`  关键位 \`$${r.level}\``);
    lines.push("");
  }

  const stamp = `${(nowUtc.getUTCMonth()+1).toString().padStart(2,'0')}-${nowUtc.getUTCDate().toString().padStart(2,'0')} ${nowUtc.getUTCHours().toString().padStart(2,'0')}:${nowUtc.getUTCMinutes().toString().padStart(2,'0')}`;
  lines.push(`_${stamp} UTC · 仅监控提醒，不自动下单_`);

  await sendTG(env, lines.join("\n"));
  console.log(`Sent: ${total} alerts`);
}
