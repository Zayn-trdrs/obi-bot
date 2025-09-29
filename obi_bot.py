import os
import time
import requests

# ================= Config =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Binance ETHUSDT order book endpoint
BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol=ETHUSDT&limit=5000"

# Wall & distance thresholds
MIN_WALL_USDT = 100000      # ignore small walls
MIN_TP_DISTANCE = 0.005     # 0.5% minimum TP distance
MIN_SL_DISTANCE = 0.003     # 0.3% minimum SL distance
# ==========================================


def fetch_order_book():
    """Fetch ETHUSDT order book from Binance."""
    try:
        resp = requests.get(BINANCE_DEPTH_URL, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("Error fetching order book:", e)
        return None


def calculate_obi(order_book, price_range=500):
    """Calculate OBI using raw bid/ask limit orders within ±price_range."""
    bids = [(float(p), float(q)) for p, q in order_book["bids"]]
    asks = [(float(p), float(q)) for p, q in order_book["asks"]]

    # Mid price
    mid_price = (bids[0][0] + asks[0][0]) / 2
    lower = mid_price - price_range
    upper = mid_price + price_range

    # Sum bid and ask volumes (price * quantity = USDT)
    buy_volume = sum(p * q for p, q in bids if p >= lower)
    sell_volume = sum(p * q for p, q in asks if p <= upper)

    total = buy_volume + sell_volume
    if total == 0:
        return mid_price, 0, 0, 0, 0, 0

    buy_pct = (buy_volume / total) * 100
    sell_pct = (sell_volume / total) * 100
    obi = ((buy_volume - sell_volume) / total) * 100

    return mid_price, buy_volume, sell_volume, obi, buy_pct, sell_pct, bids, asks


def find_levels(mid, bids, asks, direction, obi):
    """Find TP and SL based on strong walls & OBI strength."""
    entry = mid
    tp, sl = None, None

    if direction == "LONG":
        # SL = nearest strong bid wall below
        for price, qty in bids:
            usdt = price * qty
            if usdt >= MIN_WALL_USDT and (entry - price) / entry >= MIN_SL_DISTANCE:
                sl = price
                break
        # TP = ask walls above, choose depending on OBI strength
        wall_count = 0
        for price, qty in asks:
            usdt = price * qty
            if usdt >= MIN_WALL_USDT and (price - entry) / entry >= MIN_TP_DISTANCE:
                wall_count += 1
                if (obi < 20 and wall_count == 1) or (20 <= obi < 40 and wall_count == 2) or (obi >= 40 and wall_count == 3):
                    tp = price
                    break

    elif direction == "SHORT":
        # SL = nearest strong ask wall above
        for price, qty in asks:
            usdt = price * qty
            if usdt >= MIN_WALL_USDT and (price - entry) / entry >= MIN_SL_DISTANCE:
                sl = price
                break
        # TP = bid walls below
        wall_count = 0
        for price, qty in bids:
            usdt = price * qty
            if usdt >= MIN_WALL_USDT and (entry - price) / entry >= MIN_TP_DISTANCE:
                wall_count += 1
                if (obi > -20 and wall_count == 1) or (-40 <= obi <= -20 and wall_count == 2) or (obi <= -40 and wall_count == 3):
                    tp = price
                    break

    # Fallbacks if no valid wall found
    if not tp:
        tp = entry * (1.01 if direction == "LONG" else 0.99)
    if not sl:
        sl = entry * (0.995 if direction == "LONG" else 1.005)

    # Calculate RR
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk if risk > 0 else 0

    return tp, sl, rr


def send_telegram(msg):
    """Send message to Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print("Error sending Telegram message:", e)


def main():
    while True:
        order_book = fetch_order_book()
        if order_book:
            mid, buy_vol, sell_vol, obi, buy_pct, sell_pct, bids, asks = calculate_obi(order_book)

            signal = ""
            tp, sl, rr = None, None, None

            if obi >= 10:
                signal = "📈 LONG Signal"
                tp, sl, rr = find_levels(mid, bids, asks, "LONG", obi)
            elif obi <= -10:
                signal = "📉 SHORT Signal"
                tp, sl, rr = find_levels(mid, bids, asks, "SHORT", obi)

            message = (
                f"📊 ETH OBI Report (±500 range)\n"
                f"💰 Mid Price: {mid:.2f}\n"
                f"📉 Range: {mid-500:.2f} → {mid+500:.2f}\n"
                f"🟢 Buy Volume: {buy_vol:,.2f} USDT ({buy_pct:.2f}%)\n"
                f"🔴 Sell Volume: {sell_vol:,.2f} USDT ({sell_pct:.2f}%)\n"
                f"⚖️ Net OBI: {obi:.2f}%\n"
                f"{signal}"
            )

            if signal:
                message += (
                    f"\n🎯 TP: {tp:.2f}\n"
                    f"🛑 SL: {sl:.2f}\n"
                    f"📐 RR: {rr:.2f}"
                )

            send_telegram(message)

        time.sleep(60)  # wait 60 seconds


if __name__ == "__main__":
    main()
