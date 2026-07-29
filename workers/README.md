# Cloudflare Workers 迁移记录（未完成）

## ⚠️ 已废弃：Binance 封了 Cloudflare 边缘 IP

2026-07-28 尝试迁移到 Cloudflare Workers，但发现：
- Binance `fapi.binance.com` 及所有镜像域名（fapi1/2/3）都对 Cloudflare Workers IP 返回 **HTTP 403**
- 这是币安的反爬虫策略，网上多有报告
- 无法通过 User-Agent 头绕过

**结论**：Cloudflare Workers 无法直接访问 Binance 合约 API。

## 保留原因

代码保留在此，作为将来的备用方案。如果币安放开 CF IP，或者我们要用其他数据源（例如 CoinGecko），可以复用架构。

## 当前生产系统

回到 `../monitor.py`（Python）+ `../.github/workflows/monitor.yml`（GitHub Actions）。

修复了两个关键问题：
1. **滑动窗口**：从 5min 拉到 30-40min，容忍 cron 延迟
2. **状态去重**：`state.json` 存最近 24h 触发过的信号，避免同一信号重复推送

## 已花费成本
- $0（Cloudflare 账号未部署任何计费服务）
- 一个 KV namespace（免费额度内）
- 一个 Worker 已部署但会因 fetch 失败自动 no-op，可以留着或删除
