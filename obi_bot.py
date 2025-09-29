import os
import time
import requests
import ccxt
from datetime import datetime

# Load secrets from environment
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Exchanges (spot markets)
exchanges = {
    "binance": ccxt.binance(),
    "kucoin": ccxt.kucoin(),
    "mexc": ccxt.mexc(),
    "okx": ccxt.okx(),
    "bitget": ccxt.bitget()
}

# Send Telegram messages
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("Telegram error:", e)

# Get ticker price from exchange
def get_price(exchange, symbol="ETH/USDT"):
    try:
        ticker = exchanges[exchange].fetch_ticker(symbol)
        return ticker["last"]
    except Exception as e:
        print(f"Error fetching {exchange}: {e}")
        return None

def check_arbitrage():
    prices = {}
    for ex in exchanges:
        price = get_price(ex)
        if price:
            prices[ex] = price

    messages = []
    if len(prices) > 1:
        min_ex = min(prices, key=prices.get)
        max_ex = max(prices, key=prices.get)
        spread = ((prices[max_ex] - prices[min_ex]) / prices[min_ex]) * 100

        messages.append("📊 Arbitrage Scan Results:")
        for ex, p in prices.items():
            messages.append(f"   {ex}: {p:.2f} USDT")

        messages.append(f"\n🔎 Best Buy: {min_ex} ({prices[min_ex]:.2f})")
        messages.append(f"💰 Best Sell: {max_ex} ({prices[max_ex]:.2f})")
        messages.append(f"⚖️ Spread: {spread:.2f}%")

    return "\n".join(messages)

def main():
    last_heartbeat = time.time()
    while True:
        try:
            report = check_arbitrage()
            if report:
                send_telegram(report)

            # Heartbeat every 5 minutes
            if time.time() - last_heartbeat > 300:
                send_telegram("✅ Bot is scanning exchanges...")
                last_heartbeat = time.time()

            time.sleep(60)  # check every 60 sec

        except Exception as e:
            print("Error in loop:", e)
            time.sleep(30)

if __name__ == "__main__":
    main()
