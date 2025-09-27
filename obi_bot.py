import os
import time
import requests

# Get Telegram details from environment variables (Render Env Vars)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Binance API endpoint
BINANCE_URL = "https://api.binance.com/api/v3/depth"
SYMBOL = "BTCUSDT"
LIMIT = 1000   # Max depth Binance allows

def get_orderbook(symbol=SYMBOL, limit=LIMIT):
    """Fetch order book data from Binance"""
    try:
        response = requests.get(BINANCE_URL, params={"symbol": symbol, "limit": limit}, timeout=10)
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching orderbook: {e}")
        return None

def calculate_obi(data, price_range=15000):
    """Calculate buy/sell imbalance in ± price_range around current price"""
    if not data:
        return None, None, None

    # Get current mid-price (average of best bid/ask)
    best_bid = float(data["bids"][0][0])
    best_ask = float(data["asks"][0][0])
    mid_price = (best_bid + best_ask) / 2

    lower_bound = mid_price - price_range
    upper_bound = mid_price + price_range

    buy_volume = 0
    sell_volume = 0

    # Sum buy orders (bids)
    for price, qty in data["bids"]:
        price = float(price)
        qty = float(qty)
        if lower_bound <= price <= upper_bound:
            buy_volume += price * qty

    # Sum sell orders (asks)
    for price, qty in data["asks"]:
        price = float(price)
        qty = float(qty)
        if lower_bound <= price <= upper_bound:
            sell_volume += price * qty

    total = buy_volume + sell_volume
    if total == 0:
        return 0, 0, 0

    buy_imbalance = (buy_volume / total) * 100
    sell_imbalance = (sell_volume / total) * 100
    net_obi = buy_imbalance - sell_imbalance

    return round(buy_imbalance, 2), round(sell_imbalance, 2), round(net_obi, 2)

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
        buy_imbalance, sell_imbalance, net_obi = calculate_obi(data)

        if buy_imbalance is not None:
            message = (
                f"📊 OBI Report (±15,000 USD)\n"
                f"🟢 Buy Imbalance: {buy_imbalance}%\n"
                f"🔴 Sell Imbalance: {sell_imbalance}%\n"
                f"⚖️ Net OBI: {net_obi}%"
            )
            send_telegram_message(message)

        time.sleep(60)  # Wait 60 seconds

if __name__ == "__main__":
    main()
