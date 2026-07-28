"""本地测试：直接给自己发一条 Telegram 测试消息，确认 Token 和 Chat ID 正确"""
import os
import json
import urllib.request

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT"]

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = json.dumps({
    "chat_id": CHAT_ID,
    "text": "✅ 测试消息：Bot 通道打通\n\n如果你收到这条，说明配置正确。",
}).encode()

req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=10).read()
print(resp.decode())
