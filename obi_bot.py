import os
import time
import requests
from collections import deque
from datetime import datetime

# ========== CONFIG (tweak if you want) ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol=ETHUSDT&limit=5000"
BINANCE_AGGTRADES_URL = "https://api.binance.com/api/v3/aggTrades"

PRICE_RANGE = 500  # ± for OBI
INTERVAL = 60      # seconds between runs

# OBI confirmation settings
OBI_THRESHOLD = 10.0            # same threshold you used for signalling
OBI_HISTORY_LEN = 3             # require persistence across last N readings
CVD_WINDOW_SEC = 60             # how many seconds of recent aggTrades to inspect

# Wall & distance thresholds (as requested earlier)
MIN_WALL_USDT = 100000          # wall must be at least this large to count
MIN_TP_DISTANCE = 0.005         # 0.5% minimum TP distance from entry
MIN_SL_DISTANCE = 0.003         # 0.3% minimum SL distance from entry

# ================================================

# Rolling history for OBI
obi_history = deque(maxlen=OBI_HISTORY_LEN)


def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID; message not sent.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)


def fetch_order_book():
    try:
        r = requests.get(BINANCE_DEPTH_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Depth fetch error:", e)
        return None


def fetch_recent_aggtrades(window_sec=CVD_WINDOW_SEC, limit=1000):
    """
    Fetch recent aggregated trades from Binance and return only those
    within the last `window_sec` seconds.
    """
    try:
        r = requests.get(BINANCE_AGGTRADES_URL, params={"symbol": "ETHUSDT", "limit": limit}, timeout=10)
        r.raise_for_status()
        trades = r.json()
    except Exception as e:
        print("AggTrades fetch error:", e)
        return []

    cutoff_ms = int((time.time() - window_sec) * 1000)
    recent = [t for t in trades if int(t.get("T", 0)) >= cutoff_ms]
    return recent


def calculate_obi(order_book, price_range=PRICE_RANGE):
    bids = [(float(p), float(q)) for p, q in order_book["bids"]]
    asks = [(float(p), float(q)) for p, q in order_book["asks"]]

    mid_price = (bids[0][0] + asks[0][0]) / 2.0
    lower = mid_price - price_range
    upper = mid_price + price_range

    buy_volume = sum(p * q for p, q in bids if p >= lower)
    sell_volume = sum(p * q for p, q in asks if p <= upper)

    total = buy_volume + sell_volume
    if total == 0:
        return mid_price, buy_volume, sell_volume, 0.0, 0.0, 0.0, bids, asks

    buy_pct = (buy_volume / total) * 100.0
    sell_pct = (sell_volume / total) * 100.0
    obi = ((buy_volume - sell_volume) / total) * 100.0

    return mid_price, buy_volume, sell_volume, obi, buy_pct, sell_pct, bids, asks


def compute_cvd_from_aggtrades(aggtrades):
    """
    From aggTrades list compute signed notional:
      if 'm' == False => taker was buyer => positive (market buy)
      if 'm' == True => taker was seller => negative (market sell)
    Return net_notional (signed), total_notional (abs), and pct_net
    """
    net = 0.0
    total = 0.0
    for t in aggtrades:
        try:
            price = float(t["p"])
            qty = float(t["q"])
            notional = price * qty
            isMaker = t.get("m", False)
            # m == True => buyer was maker => taker was seller => negative
            signed = -notional if isMaker else notional
            net += signed
            total += notional
        except Exception:
            continue
    pct = (net / total * 100.0) if total > 0 else 0.0
    return net, total, pct


def find_tp_sl(mid, obi, bids, asks):
    """
    Wall-based TP/SL chosen per your rules:
      - primary method: pick strong walls >= MIN_WALL_USDT and satisfying min distance
      - choose 1st/2nd/3rd wall for TP depending on OBI strength buckets
      - SL is nearest strong opposite wall satisfying min SL distance
      - fall back to percentage distances if no valid walls found
    Returns: (direction, tp, sl, rr, reason)
    direction: "LONG", "SHORT", or None
    reason: text explaining if CONFIRMED or why unconfirmed (used later)
    """
    entry = mid
    direction = None
    tp = None
    sl = None

    # prepare sorted lists (asks ascend, bids descend)
    asks_sorted = sorted(asks, key=lambda x: x[0])
    bids_sorted = sorted(bids, key=lambda x: -x[0])

    if obi >= OBI_THRESHOLD:
        direction = "LONG"
        # find SL: first bid level BELOW entry with sufficient wall size & min distance
        for price, qty in bids_sorted:
            if price >= entry:
                continue
            usdt = price * qty
            dist = (entry - price) / entry
            if usdt >= MIN_WALL_USDT and dist >= MIN_SL_DISTANCE:
                sl = price
                break
        # find TP: scan asks above entry and count qualifying walls
        count = 0
        for price, qty in asks_sorted:
            if price <= entry:
                continue
            usdt = price * qty
            dist = (price - entry) / entry
            if usdt >= MIN_WALL_USDT and dist >= MIN_TP_DISTANCE:
                count += 1
                # choose TP by OBI buckets
                if (obi < 20 and count == 1) or (20 <= obi < 40 and count == 2) or (obi >= 40 and count == 3):
                    tp = price
                    break

    elif obi <= -OBI_THRESHOLD:
        direction = "SHORT"
        # SL = first ask level ABOVE entry with sufficient wall size & min distance
        for price, qty in asks_sorted:
            if price <= entry:
                continue
            usdt = price * qty
            dist = (price - entry) / entry
            if usdt >= MIN_WALL_USDT and dist >= MIN_SL_DISTANCE:
                sl = price
                break
        # TP = bid walls below entry
        count = 0
        for price, qty in bids_sorted:
            if price >= entry:
                continue
            usdt = price * qty
            dist = (entry - price) / entry
            if usdt >= MIN_WALL_USDT and dist >= MIN_TP_DISTANCE:
                count += 1
                if (obi > -20 and count == 1) or (-40 < obi <= -20 and count == 2) or (obi <= -40 and count == 3):
                    tp = price
                    break

    # Fallbacks (if no valid wall found)
    if tp is None:
        tp = entry * (1.01 if direction == "LONG" else 0.99)  # +1% / -1%
    if sl is None:
        sl = entry * (0.995 if direction == "LONG" else 1.005)  # -0.5% / +0.5%

    # RR compute (reward / risk)
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = (reward / risk) if risk > 0 else 0.0

    return direction, tp, sl, rr


def format_usdt(x):
    try:
        x = float(x)
        if x >= 1_000_000:
            return f"{x/1_000_000:.2f}M"
        if x >= 1000:
            return f"{x:,.0f}"
        return f"{x:.2f}"
    except:
        return str(x)


def main():
    last_heartbeat = 0
    send_telegram("🔔 OBI bot (enhanced) started — running with confirmation checks.")
    while True:
        try:
            order_book = fetch_order_book()
            if not order_book:
                time.sleep(INTERVAL)
                continue

            # OBI calc (identical to previous logic)
            mid, buy_vol, sell_vol, obi, buy_pct, sell_pct, bids, asks = calculate_obi(order_book)
            obi_history.append(obi)
            avg_obi = sum(obi_history) / len(obi_history) if len(obi_history) > 0 else obi

            # compute CVD from recent trades
            agg = fetch_recent_aggtrades(window_sec=CVD_WINDOW_SEC, limit=1000)
            cvd_net, cvd_total, cvd_pct = compute_cvd_from_aggtrades(agg)

            # figure TP/SL/RR
            direction, tp, sl, rr = find_tp_sl(mid, obi, bids, asks)

            # Confirmation logic:
            confirmed = False
            confirm_reasons = []
            # require average OBI exceed threshold in the direction
            if direction == "LONG" and avg_obi >= OBI_THRESHOLD:
                confirm_reasons.append(f"avg OBI {avg_obi:.2f}% >= {OBI_THRESHOLD}")
            if direction == "SHORT" and avg_obi <= -OBI_THRESHOLD:
                confirm_reasons.append(f"avg OBI {avg_obi:.2f}% <= -{OBI_THRESHOLD}")
            # require CVD direction to match (net >0 for buys, <0 for sells)
            if direction == "LONG":
                if cvd_net > 0:
                    confirm_reasons.append(f"CVD net +{format_usdt(cvd_net)} confirms buys")
                    confirmed = True if len(confirm_reasons) >= 2 else False
                else:
                    confirm_reasons.append(f"CVD net {format_usdt(cvd_net)} contradicts buys")
            elif direction == "SHORT":
                if cvd_net < 0:
                    confirm_reasons.append(f"CVD net {format_usdt(cvd_net)} confirms sells")
                    confirmed = True if len(confirm_reasons) >= 2 else False
                else:
                    confirm_reasons.append(f"CVD net {format_usdt(cvd_net)} contradicts sells")

            # Build message (always send signal if OBI crosses threshold; mark confirmation)
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            header = f"📊 ETH OBI Report (±{PRICE_RANGE} range)\n{timestamp}\n"
            body = (
                f"💰 Mid Price: {mid:.2f}\n"
                f"🟢 Buy Volume: {format_usdt(buy_vol)} USDT ({buy_pct:.2f}%)\n"
                f"🔴 Sell Volume: {format_usdt(sell_vol)} USDT ({sell_pct:.2f}%)\n"
                f"⚖️ Net OBI: {obi:.2f}%  | avg({len(obi_history)}) {avg_obi:.2f}%\n"
            )

            signal_text = ""
            if direction == "LONG":
                signal_text = "📈 LONG Signal"
            elif direction == "SHORT":
                signal_text = "📉 SHORT Signal"
            else:
                signal_text = ""

            confirm_text = "✅ CONFIRMED" if confirmed else "⚠️ UNCONFIRMED"

            signal_block = ""
            if direction:
                signal_block = (
                    f"{signal_text}  — {confirm_text}\n"
                    f"🎯 Entry: {mid:.2f}\n"
                    f"🛑 SL: {sl:.2f}\n"
                    f"✅ TP: {tp:.2f}\n"
                    f"📐 RR: {rr:.2f}\n"
                    f"Notes: {', '.join(confirm_reasons)}\n"
                    f"CVD net {format_usdt(cvd_net)} (pct {cvd_pct:+.2f}%)"
                )

            message = header + body + ("\n" + signal_block if signal_block else "")

            send_telegram(message)

            # heartbeat
            if time.time() - last_heartbeat > 300:
                send_telegram("✅ Bot heartbeat: running (OBI + CVD confirmation active).")
                last_heartbeat = time.time()

        except Exception as e:
            print("Main loop error:", e)
            try:
                send_telegram(f"⚠️ Bot error: {e}")
            except:
                pass

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
