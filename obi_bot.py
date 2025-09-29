import os
import time
import requests
from binance.client import Client

# ---------------- CONFIG ----------------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")   # default BTCUSDT
DEPTH_LIMIT = int(os.getenv("DEPTH_LIMIT", 5))
THRESHOLD = float(os.getenv("THRESHOLD", 100))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
INTERVAL = int(os.getenv("INTERVAL", 60))  # in seconds
# ----------------------------------------

client = Client(API_KEY, API_SECRET)

prev_book = None

def calculate_ofi(prev, curr):
    """Hasbrouck Order Flow Imbalance (OFI)"""
    ofi = 0
    for i in range(min(len(prev["bids"]), len(curr["bids"]))):
        bid_prev, size_prev = float(prev["bids"][i][0]), float(prev["bids"][i][1])
        bid_curr, size_curr = float(curr["bids"][i][0]), float(curr["bids"][i][1])
        if bid_curr >= bid_prev:
            ofi += (size_curr - size_prev)

    for i in range(min(len(prev["asks"]), len(curr["asks"]))):
        ask_prev, size_prev = float(prev["asks"][i][0]), float(prev["asks"][i][1])
        ask_curr, size_curr = float(curr["asks"][i][0]), float(curr["asks"][i][1])
        if ask_curr <= ask_prev:
            ofi -= (size_curr - size_prev)
    return ofi

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print("Telegram response:", r.text)
    except Exception as e:
        print("Telegram error:", e)

def run():
    global prev_book
    while True:
        try:
            curr_book = client.get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT)
            if prev_book:
                ofi = calculate_ofi(prev_book, curr_book)

                # Always log + send debug
                debug_msg = f"🔍 OFI={ofi:.2f} | Threshold={THRESHOLD}"
                print(debug_msg)
                send_telegram(debug_msg)

                if ofi > THRESHOLD:
                    send_telegram("📈 BUY SIGNAL")
                elif ofi < -THRESHOLD:
                    send_telegram("📉 SELL SIGNAL")
                else:
                    send_telegram("⚖️ Neutral")

            prev_book = curr_book
            time.sleep(INTERVAL)

        except Exception as e:
            error_msg = f"⚠️ Error: {e}"
            print(error_msg)
            send_telegram(error_msg)
            time.sleep(INTERVAL)

if __name__ == "__main__":
    send_telegram(f"🤖 Bot started for {SYMBOL}")
    run()
