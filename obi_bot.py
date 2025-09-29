import os
import time
import requests

# Load secrets from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Binance ETHUSDT order book endpoint
BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol=ETHUSDT&limit=5000"

def fetch_order_book():
    """Fetch ETHUSDT order book from Binance."""
    try:
        resp = requests.get(BINANCE_DEPTH_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("Error fetching order book:", e)
        return None

def calculate_obi(order_book, price_range=500):
    """Calculate OBI using raw bid/ask limit orders within ±price_range."""
    bids = [(float(p), float(q)) for p, q in order_book["bids"]]
    asks = [(float(p), float(q)) for p, q in order_book["asks"]]

    # Mid price
    mid_price = (bids[0][0] + asks[0][0]) / 2
    lower = mid_price - price_range
    upper = mid_price + price_range

    # Sum bid and ask volumes (price * quantity = USDT)
    buy_volume = sum(p * q for p, q in bids if p >= lower)
    sell_volume = sum(p * q for p, q in asks if p <= upper)

    total = buy_volume + sell_volume
    if total == 0:
        return mid_price, 0, 0, 0

    buy_pct = (buy_volume / total) * 100
    sell_pct = (sell_volume / total) * 100
    obi = ((buy_volume - sell_volume) / total) * 100

    return mid_price, buy_volume, sell_volume, obi, buy_pct, sell_pct

def send_telegram(msg):
    """Send message to Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Error sending Telegram message:", e)

def main():
    while True:
        order_book = fetch_order_book()
        if order_book:
            mid, buy_vol, sell_vol, obi, buy_pct, sell_pct = calculate_obi(order_book)

            signal = ""
            if obi >= 10:
                signal = "📈 LONG Signal"
            elif obi <= -10:
                signal = "📉 SHORT Signal"

            message = (
                f"📊 ETH OBI Report (±500 range)\n"
                f"💰 Mid Price: {mid:.2f}\n"
                f"📉 Range: {mid-500:.2f} → {mid+500:.2f}\n"
                f"🟢 Buy Volume: {buy_vol:,.2f} USDT ({buy_pct:.2f}%)\n"
                f"🔴 Sell Volume: {sell_vol:,.2f} USDT ({sell_pct:.2f}%)\n"
                f"⚖️ Net OBI: {obi:.2f}%\n"
                f"{signal}"
            )
            send_telegram(message)

        time.sleep(60)  # wait 60 seconds

if __name__ == "__main__":
    main()
