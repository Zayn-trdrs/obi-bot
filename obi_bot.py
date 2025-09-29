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
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
        print(r.json())  # log response in Render logs
    except Exception as e:
        print("Error sending:", e)

if __name__ == "__main__":
    while True:
        send_message("Worker alive ✅ (via proxy)")
        time.sleep(60)  # wait 1 minute
