import numpy as np
import pandas as pd
import requests
import json
import os
import talib
from datetime import datetime

# ===============================
# CONFIG
# ===============================
SYMBOL = "BTCUSDT"
POSITION_RISK = 0.02
ATR_PERIOD = 14
OBI_LEVELS = 10
TMS_THRESHOLD = 0.02
Z_THRESHOLD = 1.5
EXHAUSTION_Z = 2
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

STATE_FILE = "bot_state.json"  # persistent storage for cooldown

# ===============================
# HELPER FUNCTIONS
# ===============================

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})

def get_order_book(symbol, limit=OBI_LEVELS):
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}"
    data = requests.get(url).json()
    bids = np.array([[float(price), float(qty)] for price, qty in data['bids']])
    asks = np.array([[float(price), float(qty)] for price, qty in data['asks']])
    return bids, asks

def get_klines(symbol, interval="1m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_asset_volume","num_trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    return df.astype(float)

def calculate_weighted_obi(bids, asks):
    w = 1 / (np.arange(1, len(bids)+1))
    return np.sum((bids[:,1] - asks[:,1]) * w) / np.sum(w)

def calculate_z_score(series):
    if len(series) < 2:
        return 0
    return (series[-1] - np.mean(series)) / np.std(series)

def calculate_tms(df):
    delta_p = (df['close'].iloc[-1] - df['close'].iloc[-2]) / df['close'].iloc[-2]
    delta_v = (df['volume'].iloc[-1] - df['volume'].iloc[-2]) / df['volume'].iloc[-2]
    delta_cvd = (df['taker_buy_base'].iloc[-1] - df['taker_buy_base'].iloc[-2]) / (df['taker_buy_base'].iloc[-2]+1e-6)
    alpha, beta, gamma = 0.5, 0.3, 0.2
    return alpha*delta_p + beta*delta_v + gamma*delta_cvd

def calculate_atr(df, period=ATR_PERIOD):
    high, low, close = df['high'].values, df['low'].values, df['close'].values
    atr = talib.ATR(high, low, close, timeperiod=period)
    return atr[-1]

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_signal": None, "timestamp": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ===============================
# MAIN LOGIC
# ===============================

def main():
    try:
        state = load_state()

        # Fetch data
        bids, asks = get_order_book(SYMBOL)
        df = get_klines(SYMBOL)

        # Weighted OBI
        WOBI = calculate_weighted_obi(bids, asks)
        if "obi_history" not in state:
            state["obi_history"] = []
        state["obi_history"].append(WOBI)
        if len(state["obi_history"]) > 20:
            state["obi_history"] = state["obi_history"][-20:]
        z_obi = calculate_z_score(state["obi_history"])

        # Trend Momentum Score
        tms = calculate_tms(df)

        # CVD exhaustion
        cvd = df['taker_buy_base'].iloc[-1] - df['taker_buy_base'].iloc[-2]
        if "cvd_history" not in state:
            state["cvd_history"] = []
        state["cvd_history"].append(cvd)
        if len(state["cvd_history"]) > 20:
            state["cvd_history"] = state["cvd_history"][-20:]
        z_cvd = calculate_z_score(state["cvd_history"])

        # ATR-based position size
        atr = calculate_atr(df)
        account_balance = 1000
        position_size = (account_balance * POSITION_RISK) / (atr+1e-6)

        # Cooldown check: 1-minute cooldown
        cooldown_seconds = 60
        last_signal_time = state.get("timestamp", 0)
        now_ts = int(datetime.now().timestamp())
        if now_ts - last_signal_time < cooldown_seconds:
            print("Cooldown active, skipping signal")
            save_state(state)
            return

        # Signal conditions
        signal_sent = False
        if z_obi > Z_THRESHOLD and tms > TMS_THRESHOLD and z_cvd < EXHAUSTION_Z:
            send_telegram_message(f"🚀 LONG | Size: {position_size:.4f} | WOBI z:{z_obi:.2f} TMS:{tms:.4f}")
            state["last_signal"] = "LONG"
            signal_sent = True

        elif z_obi < -Z_THRESHOLD and tms < -TMS_THRESHOLD and z_cvd > -EXHAUSTION_Z:
            send_telegram_message(f"🔻 SHORT | Size: {position_size:.4f} | WOBI z:{z_obi:.2f} TMS:{tms:.4f}")
            state["last_signal"] = "SHORT"
            signal_sent = True

        if signal_sent:
            state["timestamp"] = now_ts

        save_state(state)

    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    main()
