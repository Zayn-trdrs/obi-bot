import time
import requests
from datetime import datetime

# === CONFIG ===
TOKEN = "YOUR_BOT_TOKEN"  # put your token here
CHAT_ID = "YOUR_CHAT_ID"  # your Telegram user/chat ID
API_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

def send_message(text):
    try:
        payload = {"chat_id": CHAT_ID, "text": text}
        r = requests.post(API_URL, data=payload)
        print("Message status:", r.json())  # log response
    except Exception as e:
        print("Error sending:", e)

def check_ofi_signal():
    """
    Your Order Flow Imbalance logic goes here.
    For now, I’ll simulate a fake condition.
    Replace this with your real OFI strategy.
    """
    # Example: if imbalance > threshold, return signal
    imbalance = 0  # <-- replace with real calculation
    threshold = 10
    if imbalance > threshold:
        return f"📊 OFI Signal Detected! Imbalance = {imbalance}"
    else:
        return None

def main():
    while True:
        # Always send heartbeat
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_message(f"✅ Bot alive at {now}")

        # Check for OFI strategy signal
        signal = check_ofi_signal()
        if signal:
            send_message(signal)

        time.sleep(60)  # wait 60s before next loop

if __name__ == "__main__":
    main()
