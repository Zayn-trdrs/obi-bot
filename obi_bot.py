import time
import schedule
import telebot
from binance.client import Client

# ---------------- CONFIG ----------------
API_KEY = "your_binance_api_key"
API_SECRET = "your_binance_api_secret"
SYMBOL = "BTCUSDT"
DEPTH_LIMIT = 5
THRESHOLD = 100
TELEGRAM_TOKEN = "your_telegram_bot_token"
CHAT_ID = "your_chat_id"
# ----------------------------------------

client = Client(API_KEY, API_SECRET)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

prev_book = None

def calculate_ofi(prev, curr):
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

def job():
    global prev_book
    try:
        curr_book = client.get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT)
        if prev_book:
            ofi = calculate_ofi(prev_book, curr_book)
            if ofi > THRESHOLD:
                bot.send_message(CHAT_ID, f"📈 BUY SIGNAL: OFI={ofi:.2f}")
            elif ofi < -THRESHOLD:
                bot.send_message(CHAT_ID, f"📉 SELL SIGNAL: OFI={ofi:.2f}")
            else:
                bot.send_message(CHAT_ID, f"⚖️ Neutral: OFI={ofi:.2f}")
        prev_book = curr_book
    except Exception as e:
        bot.send_message(CHAT_ID, f"⚠️ Error: {e}")

# Schedule every 1 minute
schedule.every(1).minutes.do(job)

while True:
    schedule.run_pending()
    time.sleep(1)
