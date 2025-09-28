import os
import time
import requests

# Get Telegram details from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Binance API endpoint
BINANCE_URL = "https://api.binance.com/api/v3/depth"
SYMBOL = "ETHUSDT"
LIMIT = 1000   # fetch up to 1000 levels

def get_orderbook(symbol=SYMBOL, limit=LIMIT):
    """Fetch order book data from Binance"""
    try:
        response = requests.get(BINANCE_URL, params={"symbol": symbol, "limit": limit}, timeout=10)
        return response.json()
    except Exception as e:
        print(f"Error fetching orderbook: {e}")
        return None

def calculate_obi(data, price_range=500):
    """Calculate OBI within ±price_range around mid price"""
    if not data:
        return None, None, None, None, None, None, None

    bids = [(float(p), float(q)) for p, q in data["bids"]]
    asks = [(float(p), float(q)) for p, q in data["asks"]]

    # Mid price = average of best bid & ask
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    mid_price = (best_bid + best_ask) / 2

    lower_bound = mid_price - price_range
    upper_bound = mid_price + price_range

    buy_volume = sum(p * q for p, q in bids if lower_bound <= p <= upper_bound)
    sell_volume = sum(p * q for p, q in asks if lower_bound <= p <= upper_bound)
    total = buy_volume + sell_volume

    if total == 0:
        return buy_volume, sell_volume, 0, 0, 0, mid_price, (lower_bound, upper_bound)

    buy_pct = (buy_volume / total) * 100
    sell_pct = (sell_volume / total) * 100
    net_obi = buy_pct - sell_pct

    return round(buy_volume, 2), round(sell_volume, 2), round(buy_pct, 2), round(sell_pct, 2), round(net_obi, 2), round(mid_price, 2), (round(lower_bound, 2), round(upper_bound, 2))

def send_telegram_message(message):
    """Send message to Telegram bot"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def main():
    while True:
        data = get_orderbook()
        buy_vol, sell_vol, buy_pct, sell_pct, net_obi, mid_price, (lower, upper) = calculate_obi(data)

        if buy_vol is not None:
            message = (
                f"📊 ETH OBI Report (±500 range)\n"
                f"💰 Mid Price: {mid_price}\n"
                f"📉 Range: {lower} → {upper}\n\n"
                f"🟢 Buy Volume: {buy_vol:,.2f} USDT ({buy_pct}%)\n"
                f"🔴 Sell Volume: {sell_vol:,.2f} USDT ({sell_pct}%)\n"
                f"⚖️ Net OBI: {net_obi}%"
            )
            send_telegram_message(message)

        time.sleep(60)  # Wait 60 seconds

if __name__ == "__main__":
    main()
