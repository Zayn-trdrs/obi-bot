import requests
import time
import hmac
import hashlib
import json
from telegram import Bot

# =========================
# CONFIG
# =========================
TELEGRAM_BOT_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'
SYMBOL = 'BTCUSDT'  # Trading pair
DEPTH_LIMIT = 10    # Number of levels to calculate OFI
OFI_THRESHOLD = 10  # Percent threshold to trigger signal
TP_MULTIPLIER = 2   # TP = RR * SL
SL_PERCENT = 0.2    # Stop loss in percent

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# =========================
# HELPER FUNCTIONS
# =========================
def get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT):
    url = f'https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}'
    response = requests.get(url)
    data = response.json()
    return data

def calculate_ofi(order_book):
    bid_volume = sum([float(level[1]) for level in order_book['bids']])
    ask_volume = sum([float(level[1]) for level in order_book['asks']])
    ofi = bid_volume - ask_volume
    ofi_percent = ofi / (bid_volume + ask_volume) * 100
    return ofi_percent

def send_telegram(message):
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def generate_signal(ofi_percent, last_price):
    # Determine direction
    if ofi_percent > OFI_THRESHOLD:
        direction = 'LONG'
        entry = last_price
        sl = entry * (1 - SL_PERCENT/100)
        tp = entry + (entry - sl) * TP_MULTIPLIER
    elif ofi_percent < -OFI_THRESHOLD:
        direction = 'SHORT'
        entry = last_price
        sl = entry * (1 + SL_PERCENT/100)
        tp = entry - (sl - entry) * TP_MULTIPLIER
    else:
        return None
    
    message = (
        f"📈 OFI SIGNAL 📉\n"
        f"Pair: {SYMBOL}\n"
        f"Direction: {direction}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {sl:.2f}\n"
        f"Take Profit: {tp:.2f}\n"
        f"OFI%: {ofi_percent:.2f}%"
    )
    return message

# =========================
# MAIN LOOP
# =========================
if __name__ == '__main__':
    print("🚀 OFI Telegram Bot Started")
    last_signal = None
    while True:
        try:
            order_book = get_order_book()
            ofi_percent = calculate_ofi(order_book)
            last_price = (float(order_book['bids'][0][0]) + float(order_book['asks'][0][0])) / 2
            signal_message = generate_signal(ofi_percent, last_price)
            
            # Avoid sending duplicate signals
            if signal_message and signal_message != last_signal:
                send_telegram(signal_message)
                last_signal = signal_message
                print(signal_message)
            
            time.sleep(1)  # check every second
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)
