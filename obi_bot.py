import os
import time
import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Telegram API
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(text):
    url = f"{BASE_URL}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print(r.json())
    except Exception as e:
        print("Error sending message:", e)

# Fetch Binance order book
def get_orderbook(symbol="BTCUSDT", limit=5):
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}"
    r = requests.get(url, timeout=10)
    return r.json()

# Compute simple OFI (Order Flow Imbalance)
def calculate_ofi(orderbook):
    bids = sum(float(b[1]) for b in orderbook["bids"])
    asks = sum(float(a[1]) for a in orderbook["asks"])
    return bids - asks   # Positive = buy pressure, Negative = sell pressure

if __name__ == "__main__":
    print("🚀 Bot with OFI strategy started")
    while True:
        try:
            # 1. Get order book
            ob = get_orderbook("BTCUSDT", limit=5)

            # 2. Calculate OFI
            ofi = calculate_ofi(ob)

            # 3. Generate signal
            if ofi > 50:   # Threshold for BUY
                send_message(f"📈 BUY signal (OFI={ofi:.2f}) on BTCUSDT")
            elif ofi < -50:  # Threshold for SELL
                send_message(f"📉 SELL signal (OFI={ofi:.2f}) on BTCUSDT")
            else:
                send_message(f"Worker alive ✅ | OFI={ofi:.2f}")

        except Exception as e:
            send_message(f"⚠️ Error: {e}")

        time.sleep(60)  # wait 1 min
