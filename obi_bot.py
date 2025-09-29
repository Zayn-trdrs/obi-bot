import time
import requests
import telebot
from binance.client import Client

# ---------------- CONFIG ----------------
API_KEY = "your_binance_api_key"
API_SECRET = "your_binance_api_secret"
SYMBOL = "BTCUSDT"  # change to your pair
DEPTH_LIMIT = 5     # how many levels of orderbook to fetch
INTERVAL = 60       # seconds
THRESHOLD = 100     # imbalance threshold (tune this)
TELEGRAM_TOKEN = "your_telegram_bot_token"
CHAT_ID = "your_chat_id"
# ----------------------------------------

client = Client(API_KEY, API_SECRET)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- Hasbrouck OFI calculation ---
def calculate_ofi(prev, curr):
    """
    Hasbrouck’s OFI approximation:
    OFI = sum(ΔBidSize if BidPrice ↑ or same, ΔAskSize if AskPrice ↓ or same)
    """
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

def send_signal(message):
    bot.send_message(CHAT_ID, message)

# --- Main loop ---
def run():
    prev_book = client.get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT)
    time.sleep(INTERVAL)

    while True:
        try:
            curr_book = client.get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT)
            ofi = calculate_ofi(prev_book, curr_book)

            if ofi > THRESHOLD:
                send_signal(f"📈 BUY SIGNAL: OFI={ofi:.2f}")
            elif ofi < -THRESHOLD:
                send_signal(f"📉 SELL SIGNAL: OFI={ofi:.2f}")
            else:
                send_signal(f"⚖️ Neutral: OFI={ofi:.2f}")

            prev_book = curr_book
            time.sleep(INTERVAL)

        except Exception as e:
            send_signal(f"⚠️ Error: {e}")
            time.sleep(INTERVAL)

if __name__ == "__main__":
    run()
