import numpy as np
import pandas as pd
import requests
from datetime import datetime
import talib

# ===============================
# CONFIGURATION
# ===============================
API_KEY = "YOUR_EXCHANGE_API_KEY"
API_SECRET = "YOUR_EXCHANGE_SECRET"
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

SYMBOL = "BTCUSDT"
POSITION_RISK = 0.02  # 2% of capital per trade
ATR_PERIOD = 14
OBI_LEVELS = 10  # Top N levels of order book
TMS_THRESHOLD = 0.02  # Trend Momentum Score threshold
Z_THRESHOLD = 1.5  # Weighted OBI z-score
EXHAUSTION_Z = 2  # CVD exhaustion z-score

# ===============================
# FUNCTIONS
# ===============================

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})

def get_order_book(symbol, limit=OBI_LEVELS):
    # Replace with your exchange API endpoint
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}"
    data = requests.get(url).json()
    bids = np.array([[float(price), float(qty)] for price, qty in data['bids']])
    asks = np.array([[float(price), float(qty)] for price, qty in data['asks']])
    return bids, asks

def calculate_weighted_obi(bids, asks):
    # Weighted OBI formula
    w = 1 / (np.arange(1, len(bids)+1))
    WOBI = np.sum((bids[:,1] - asks[:,1]) * w) / np.sum(w)
    return WOBI

def calculate_z_score(series):
    return (series[-1] - np.mean(series)) / np.std(series)

def get_historical_klines(symbol, interval="1m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_asset_volume","num_trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    df = df.astype(float)
    return df

def calculate_tms(df):
    # Trend Momentum Score: weighted sum of price, volume, CVD change
    delta_p = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
    delta_v = (df['volume'].iloc[-1] - df['volume'].iloc[-2]) / df['volume'].iloc[-2]
    delta_cvd = (df['taker_buy_base'].iloc[-1] - df['taker_buy_base'].iloc[-2]) / (df['taker_buy_base'].iloc[-2] + 1e-6)
    alpha, beta, gamma = 0.5, 0.3, 0.2
    tms = alpha*delta_p + beta*delta_v + gamma*delta_cvd
    return tms

def calculate_atr(df, period=ATR_PERIOD):
    high = df['high'].values
    low = df['low'].values
    close = df['close'].values
    atr = talib.ATR(high, low, close, timeperiod=period)
    return atr[-1]

# ===============================
# MAIN LOGIC
# ===============================

obi_history = []
cvd_history = []

while True:
    try:
        # Fetch data
        bids, asks = get_order_book(SYMBOL)
        df = get_historical_klines(SYMBOL)

        # Weighted OBI & z-score
        WOBI = calculate_weighted_obi(bids, asks)
        obi_history.append(WOBI)
        if len(obi_history) < 20:
            continue
        z_obi = calculate_z_score(obi_history[-20:])

        # Trend Momentum Score
        tms = calculate_tms(df)

        # CVD exhaustion filter
        cvd = df['taker_buy_base'].iloc[-1] - df['taker_buy_base'].iloc[-2]
        cvd_history.append(cvd)
        z_cvd = calculate_z_score(cvd_history[-20:])

        # ATR-based dynamic position size
        atr = calculate_atr(df)
        account_balance = 1000  # Example, replace with live balance
        position_size = (account_balance * POSITION_RISK) / (atr + 1e-6)

        # ===============================
        # SIGNAL CONDITIONS
        # ===============================
        if z_obi > Z_THRESHOLD and tms > TMS_THRESHOLD and z_cvd < EXHAUSTION_Z:
            message = f"🚀 LONG SIGNAL | Size: {position_size:.4f} BTC | WOBI z: {z_obi:.2f} | TMS: {tms:.4f}"
            send_telegram_message(message)

        elif z_obi < -Z_THRESHOLD and tms < -TMS_THRESHOLD and z_cvd > -EXHAUSTION_Z:
            message = f"🔻 SHORT SIGNAL | Size: {position_size:.4f} BTC | WOBI z: {z_obi:.2f} | TMS: {tms:.4f}"
            send_telegram_message(message)

    except Exception as e:
        print("Error:", e)
