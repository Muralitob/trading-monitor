# Cloudflare Workers 部署指南

## 前提
- Cloudflare 账号（免费注册 https://dash.cloudflare.com/sign-up）
- Node.js 18+（Mac 自带或从 https://nodejs.org 装）

## 一次性设置（10 分钟）

### 1. 安装 Wrangler CLI
```bash
npm install -g wrangler
```

### 2. 登录 Cloudflare
```bash
wrangler login
```
会弹浏览器让你授权，点同意就行。

### 3. 创建 KV 命名空间（用于去重）
```bash
cd /Users/mura/Documents/invest/trading-monitor/workers
wrangler kv namespace create "KV"
```
会输出类似：
```
[[kv_namespaces]]
binding = "KV"
id = "abc123..."
```
**把 id 复制到 `wrangler.toml` 里替换 `REPLACE_WITH_KV_ID`。**

### 4. 设置 Secrets（Telegram Token 和 Chat ID）
```bash
wrangler secret put TG_TOKEN
# 粘贴：8618129888:AAHnK9ZEp159a81TThUogYTwvsq9D0F8N6E

wrangler secret put TG_CHAT
# 粘贴：1626067349
```

### 5. 部署
```bash
wrangler deploy
```
输出会包含一个 URL，形如 `https://trading-monitor.YOUR_SUBDOMAIN.workers.dev`

### 6. 测试
浏览器打开：`https://trading-monitor.YOUR_SUBDOMAIN.workers.dev/test`
应该收到 Telegram 测试消息。

再打开 `/run` 手动跑一次监控。

## 之后修改代码
1. 编辑 `worker.js`
2. `wrangler deploy` 一句话搞定

## 停机
Cloudflare Dashboard → Workers → trading-monitor → Settings → Delete

## 关闭 GitHub Actions（避免重复推送）
GitHub 仓库 → Actions → Price Monitor → 三点菜单 → Disable workflow
