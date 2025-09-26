import os
import time
import requests

# Get settings from environment variables (Render dashboard)
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
MODE = os.getenv("MODE", "price_diff")   # price_diff mode
PRICE_DIFF = float(os.getenv("PRICE_DIFF", "1000"))
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "60"))

# Binance API endpoint
BINANCE_ORDERBOOK_URL = "https://api.binance.com/api/v3/depth"

def get_orderbook(symbol="BTCUSDT", limit=1000):
    url = f"{BINANCE_ORDERBOOK_URL}?symbol={symbol}&limit=1000"
    response = requests.get(url, timeout=10)
    return response.json()

def calculate_obi(orderbook, price_diff):
    bids = [(float(p), float(q)) for p, q in orderbook["bids"]]
    asks = [(float(p), float(q)) for p, q in orderbook["asks"]]

    # Current mid price
    mid_price = (bids[0][0] + asks[0][0]) / 2

    # Price range
    lower = mid_price - price_diff
    upper = mid_price + price_diff

    bid_vol = sum(q for p, q in bids if lower <= p <= mid_price)
    ask_vol = sum(q for p, q in asks if mid_price <= p <= upper)

    obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) * 100 if (bid_vol + ask_vol) > 0 else 0

    return mid_price, lower, upper, bid_vol, ask_vol, obi

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=payload, timeout=10)

def main():
    while True:
        try:
            ob = get_orderbook(SYMBOL)
            mid, lower, upper, bid, ask, obi = calculate_obi(ob, PRICE_DIFF)

            msg = (
                f"📊 OBI {SYMBOL}\n"
                f"Price: {mid:,.2f}\n"
                f"Range: {lower:,.2f} → {upper:,.2f}\n"
                f"BidVol: {bid:,.2f}\n"
                f"AskVol: {ask:,.2f}\n"
                f"OBI: {obi:.2f}%"
            )
            send_telegram(msg)
        except Exception as e:
            send_telegram(f"⚠️ Error: {str(e)}")

        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    main()
