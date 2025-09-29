import requests
import time

# =========================
# CONFIG
# =========================
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'
MIN_PROFIT_PKR = 1  # Minimum profit in PKR to alert
CHECK_INTERVAL = 10  # seconds between checks

# Exchanges and P2P API endpoints
EXCHANGES = {
    "Binance": "https://api.binance.com/api/v3/ticker/price?symbol=USDTBUSD",
    "KuCoin": "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=USDT-USDT",
    "MEXC": "https://www.mexc.com/api/v3/ticker/price?symbol=USDTUSDT",
    "OKX": "https://www.okx.com/api/spot/v3/instruments/USDT-USDT/ticker",
    # Add more endpoints as needed
}

# =========================
# HELPER FUNCTIONS
# =========================
def get_price(exchange_name, url):
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        # Adjust according to each API's structure
        if exchange_name == "Binance":
            return float(data['price'])
        elif exchange_name == "KuCoin":
            return float(data['data']['price'])
        elif exchange_name == "MEXC":
            return float(data['price'])
        elif exchange_name == "OKX":
            return float(data['last'])
        else:
            return None
    except Exception as e:
        print(f"Error fetching price from {exchange_name}: {e}")
        return None

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    requests.post(url, data=data)

# =========================
# MAIN LOOP
# =========================
if __name__ == '__main__':
    print("🚀 Arbitrage Signal Bot Started")
    while True:
        prices = {}
        for name, url in EXCHANGES.items():
            price = get_price(name, url)
            if price:
                prices[name] = price

        if len(prices) < 2:
            time.sleep(CHECK_INTERVAL)
            continue

        # Find min and max price
        buy_exchange = min(prices, key=prices.get)
        sell_exchange = max(prices, key=prices.get)
        profit = prices[sell_exchange] - prices[buy_exchange]

        if profit >= MIN_PROFIT_PKR:
            message = (
                f"💰 Arbitrage Opportunity 💰\n"
                f"Buy on: {buy_exchange} at {prices[buy_exchange]:.2f} PKR\n"
                f"Sell on: {sell_exchange} at {prices[sell_exchange]:.2f} PKR\n"
                f"Potential Profit: {profit:.2f} PKR"
            )
            send_telegram(message)
            print(message)

        time.sleep(CHECK_INTERVAL)
