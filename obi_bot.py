import requests
import time
import numpy as np
from collections import deque

# === CONFIG ===
PAIR = "ETHUSDT"
RANGE = 500  # ± range around mid price
INTERVAL = 60  # seconds
HISTORY = 50  # rolling window for Z-score, VPIN, λ

# === TELEGRAM CONFIG ===
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

# === STATE ===
obi_history = deque(maxlen=HISTORY)
flow_history = deque(maxlen=HISTORY)
price_history = deque(maxlen=HISTORY)

def fetch_orderbook():
    url = f"https://api.binance.com/api/v3/depth?symbol={PAIR}&limit=1000"
    data = requests.get(url).json()
    bids = [(float(p), float(q)) for p, q in data["bids"]]
    asks = [(float(p), float(q)) for p, q in data["asks"]]
    return bids, asks

def calculate_metrics(bids, asks):
    mid = (bids[0][0] + asks[0][0]) / 2

    # Filter within ±RANGE
    bid_vol = sum(p * q for p, q in bids if p >= mid - RANGE)
    ask_vol = sum(p * q for p, q in asks if p <= mid + RANGE)
    total_vol = bid_vol + ask_vol

    # --- OBI ---
    obi = ((bid_vol - ask_vol) / total_vol) * 100 if total_vol > 0 else 0

    # Store histories
    obi_history.append(obi)
    flow_history.append(bid_vol - ask_vol)
    price_history.append(mid)

    # --- Z-Score OBI ---
    if len(obi_history) > 10:
        mu = np.mean(obi_history)
        sigma = np.std(obi_history)
        zobi = (obi - mu) / sigma if sigma > 0 else 0
    else:
        zobi = 0

    # --- VPIN ---
    if len(flow_history) > 1:
        vpin = np.mean([abs(f) for f in flow_history]) / (np.mean([abs(f) for f in flow_history]) + 1e-9)
    else:
        vpin = 0

    # --- Kyle's Lambda ---
    if len(flow_history) > 2:
        delta_p = price_history[-1] - price_history[-2]
        delta_f = flow_history[-1]
        lambd = (delta_p / delta_f) if delta_f != 0 else 0
    else:
        lambd = 0

    return mid, bid_vol, ask_vol, obi, zobi, vpin, lambd

def interpret(obi, zobi, vpin, lambd):
    # OBI interpretation
    obi_dir = "🟢 Bullish" if obi > 0 else "🔴 Bearish"

    # ZOBI interpretation
    if zobi > 2:
        zobi_txt = f"{zobi:.2f} (Strong Bullish)"
    elif zobi < -2:
        zobi_txt = f"{zobi:.2f} (Strong Bearish)"
    else:
        zobi_txt = f"{zobi:.2f} (Neutral)"

    # VPIN interpretation
    if vpin > 0.6:
        vpin_txt = f"{vpin:.2f} (High Toxicity)"
    elif vpin > 0.3:
        vpin_txt = f"{vpin:.2f} (Medium Toxicity)"
    else:
        vpin_txt = f"{vpin:.2f} (Low Toxicity)"

    # Lambda interpretation
    if abs(lambd) > 0.005:
        lambd_txt = f"{lambd:.4f} (Fragile Market)"
    elif abs(lambd) > 0.001:
        lambd_txt = f"{lambd:.4f} (Normal Impact)"
    else:
        lambd_txt = f"{lambd:.4f} (Stable Market)"

    return obi_dir, zobi_txt, vpin_txt, lambd_txt

def send_message(msg):
    payload = {"chat_id": CHAT_ID, "text": msg}
    requests.post(TELEGRAM_URL, data=payload)

def main():
    while True:
        try:
            bids, asks = fetch_orderbook()
            mid, bid_vol, ask_vol, obi, zobi, vpin, lambd = calculate_metrics(bids, asks)
            obi_dir, zobi_txt, vpin_txt, lambd_txt = interpret(obi, zobi, vpin, lambd)

            msg = (
                f"📊 {PAIR} OBI Report (±{RANGE} range)\n"
                f"💰 Mid Price: {mid:.2f}\n"
                f"🟢 Buy Vol: {bid_vol:,.2f} USDT\n"
                f"🔴 Sell Vol: {ask_vol:,.2f} USDT\n"
                f"⚖️ Net OBI: {obi:.2f}% ({obi_dir})\n\n"
                f"📈 Advanced Stats:\n"
                f"🔹 ZOBI: {zobi_txt}\n"
                f"🔹 VPIN: {vpin_txt}\n"
                f"🔹 λ (Impact): {lambd_txt}"
            )

            send_message(msg)
        except Exception as e:
            send_message(f"⚠️ Error: {e}")

        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
