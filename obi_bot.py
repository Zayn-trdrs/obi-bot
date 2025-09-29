import time
import requests
from datetime import datetime

# === CONFIG ===
TOKEN = "YOUR_BOT_TOKEN"  # keep same as your old working script
CHAT_ID = "YOUR_CHAT_ID"  # keep same as your old working script
API_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

def send_message(text):
    try:
        r = requests.post(API_URL, data={"chat_id": CHAT_ID, "text": text})
        print("Telegram response:", r.json())
    except Exception as e:
        print("Error:", e)

def calculate_ofi():
    """
    Replace this dummy logic with your real OFI calculation.
    For now, I’ll just simulate a random imbalance.
    """
    import random
    imbalance = random.randint(-20, 20)
    return imbalance

def main():
    while True:
        # Always send a heartbeat so you know it’s alive
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_message(f"✅ Bot alive at {now}")

        # OFI strategy
        imbalance = calculate_ofi()
        threshold = 10
        if imbalance > threshold:
            send_message(f"📊 Buy Signal! OFI={imbalance}")
        elif imbalance < -threshold:
            send_message(f"📉 Sell Signal! OFI={imbalance}")
        else:
            send_message(f"ℹ️ No trade. OFI={imbalance}")

        time.sleep(60)

if __name__ == "__main__":
    main()
