import os
import time
import requests

# Load environment variables (set these in Render dashboard)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Proxy endpoint instead of api.telegram.org
BASE_URL = f"https://tgapi-proxy.moonshot.dev/bot{BOT_TOKEN}"

def send_message(text):
    url = f"{BASE_URL}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print("Request sent to:", url)  # log endpoint
        print("Status code:", r.status_code)
        print("Response:", r.text)      # print raw response from Telegram/proxy
    except Exception as e:
        print("Error sending message:", e)

if __name__ == "__main__":
    print("🚀 Python bot started, sending message every 1 minute...")
    while True:
        send_message("Worker alive ✅ (via proxy)")
        time.sleep(60)  # wait 1 minute
