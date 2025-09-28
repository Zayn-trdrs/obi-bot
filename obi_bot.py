import os
import requests
import time

# Get credentials from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Binance ETHUSDT depth endpoint
BINANCE_URL = "https://api.binance.com/api/v3/depth"

def fetch_order_book(symbol="ETHUSDT", limit=5000):
    """Fetch order book data from Binance"""
    response = requests.get(BINANCE_URL, params={"symbol": symbol, "limit": limit})
    data = response.json()
    return data["bids"], data["asks"]

def calculate_bins(bids, asks, bin_size=100, price_range=500):
    """Calculate OBI using bins within ±price_range"""
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid_price = (best_bid + best_ask) / 2

    lower_bound = mid_price - price_range
    upper_bound = mid_price + price_range

    # Create bins
    num_bins = int((2 * price_range) / bin_size)
    buy_bins = [0.0] * num_bins
    sell_bins = [0.0] * num_bins

    # Fill buy bins (bids)
    for price, qty in bids:
        p, q = float(price), float(qty)
        if lower_bound <= p <= upper_bound:
            bin_index = int((p - lower_bound) / bin_size)
            buy_bins[bin_index] += p * q  # in USDT

    # Fill sell bins (asks)
    for price, qty in asks:
        p, q = float(price), float(qty)
        if lower_bound <= p <= upper_bound:
            bin_index = int((p - lower_bound) / bin_size)
            sell_bins[bin_index] += p * q  # in USDT

    # Total buy = bins below mid price
    buy_volume = sum(buy_bins[i] for i in range(len(buy_bins)) if (lower_bound + i*bin_size) < mid_price)
    # Total sell = bins above mid price
    sell_volume = sum(sell_bins[i] for i in range(len(sell_bins)) if (lower_bound + i*bin_size) > mid_price)

    total = buy_volume + sell_volume
    if total == 0:
        obi = 0
    else:
        obi = (buy_volume - sell_volume) / total * 100

    return mid_price, lower_bound, upper_bound, buy_volume, sell_volume, obi

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=payload)

def main():
    while True:
        try:
            bids, asks = fetch_order_book()
            mid, low, high, buy_vol, sell_vol, obi = calculate_bins(bids, asks)

            message = (
                f"📊 ETH OBI Report (±500 range, $100 bins)\n"
                f"💰 Mid Price: {mid:.2f}\n"
                f"📉 Range: {low:.2f} → {high:.2f}\n"
                f"🟢 Buy Volume: {buy_vol:,.2f} USDT\n"
                f"🔴 Sell Volume: {sell_vol:,.2f} USDT\n"
                f"⚖️ Net OBI: {obi:+.2f}%"
            )

            # Trading signal
            if obi >= 10:
                message += "\n📈✅ Signal: LONG (OBI > +10%)"
            elif obi <= -10:
                message += "\n📉❌ Signal: SHORT (OBI < -10%)"

            send_telegram_message(message)
            time.sleep(60)

        except Exception as e:
            send_telegram_message(f"⚠️ Bot Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
