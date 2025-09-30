#!/usr/bin/env python3
"""
Orderflow Fade Signal Bot
- Computes Order Book Imbalance (OBI) from REST snapshots (top N levels)
- Computes Cumulative Volume Delta (CVD) from Binance aggTrade websocket
- Signals when OBI and CVD diverge (possible fake push -> fade)
- Sends signals to Telegram chat

ENV VARIABLES (required):
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- SYMBOL (e.g. BTCUSDT)
Optional:
- OBI_THRESHOLD (default 0.12)  # 12% imbalance
- CVD_WINDOW (default 60)        # seconds smoothing window for CVD derivative
- TOP_LEVELS (default 10)        # top book levels to sum
- POLL_INTERVAL (default 2)      # seconds between REST orderbook snapshots
- MIN_CVD_SLOPE (default 0.00005) # min normalized slope considered "confirming"
- TIMEZONE (for logs, optional)
"""

import os
import time
import math
import asyncio
import json
import logging
from collections import deque
from typing import Deque
import requests
import aiohttp

# -----------------------
# Config from ENV
# -----------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SYMBOL = os.environ.get("SYMBOL", "BTCUSDT").upper()

# strategy params
TOP_LEVELS = int(os.environ.get("TOP_LEVELS", 10))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", 2.0))
OBI_THRESHOLD = float(os.environ.get("OBI_THRESHOLD", 0.12))  # 12%
CVD_WINDOW = int(os.environ.get("CVD_WINDOW", 60))  # seconds
MIN_CVD_SLOPE = float(os.environ.get("MIN_CVD_SLOPE", 0.00005))  # normalized
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

BINANCE_WS = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@aggTrade"
BINANCE_REST_DEPTH = f"https://api.binance.com/api/v3/depth?symbol={SYMBOL}&limit=1000"
BINANCE_REST_TICKER = f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}"

# basic checks
if TELEGRAM_BOT_TOKEN is None or TELEGRAM_CHAT_ID is None:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables before running.")

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("obi_cvd_bot")

# -----------------------
# Utilities: Telegram
# -----------------------
def send_telegram(text: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.warning(f"Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        log.exception("Telegram send error")

# -----------------------
# Orderbook imbalance
# -----------------------
def compute_obi_from_snapshot(snapshot: dict, top_n: int = 10) -> float:
    """
    snapshot: parsed JSON with 'bids' and 'asks' arrays of [price, qty]
    returns normalized imbalance in range [-1, 1]
    formula: (sum_bids - sum_asks) / (sum_bids + sum_asks)
    """
    try:
        bids = snapshot.get("bids", [])[:top_n]
        asks = snapshot.get("asks", [])[:top_n]
        sum_bids = sum(float(q) * float(p) for p, q in bids)
        sum_asks = sum(float(q) * float(p) for p, q in asks)
        denom = (sum_bids + sum_asks) or 1.0
        obi = (sum_bids - sum_asks) / denom
        return float(obi)
    except Exception as e:
        log.exception("compute_obi error")
        return 0.0

# -----------------------
# CVD tracking (async)
# -----------------------
class CVDTracker:
    def __init__(self, window_seconds=60):
        self.cvd = 0.0
        self.timestamps: Deque[tuple] = deque()  # (timestamp, cumulative_cvd)
        self.window_seconds = window_seconds
        self.lock = asyncio.Lock()

    async def add_trade(self, price: float, qty: float, mid_price: float):
        """
        Determine if trade is buy or sell aggressor:
        - if trade price >= mid_price: treat as buyer-aggressor (positive delta)
        - else seller-aggressor (negative delta)
        """
        side = 1 if price >= mid_price else -1
        delta = side * qty
        now = time.time()
        async with self.lock:
            self.cvd += delta
            self.timestamps.append((now, self.cvd))
            # prune older points
            cutoff = now - (self.window_seconds * 3)  # keep some margin
            while self.timestamps and self.timestamps[0][0] < cutoff:
                self.timestamps.popleft()

    async def get_slope(self):
        """
        Return slope (delta per second) over last window_seconds.
        Normalized by recent average not to blow up with qty units.
        """
        now = time.time()
        cutoff = now - self.window_seconds
        async with self.lock:
            # find first point >= cutoff
            pts = [p for p in self.timestamps if p[0] >= cutoff]
            if len(pts) < 2:
                return 0.0
            t0, v0 = pts[0]
            t1, v1 = pts[-1]
            dt = t1 - t0 if t1 != t0 else 1.0
            raw_slope = (v1 - v0) / dt
            # normalize by recent absolute average volume to create unitless metric
            avg_abs = (sum(abs(v) for _, v in pts) / len(pts)) if len(pts) else 1.0
            avg_abs = avg_abs or 1.0
            norm_slope = raw_slope / avg_abs
            return float(norm_slope)

# -----------------------
# Binance helpers
# -----------------------
def fetch_orderbook_snapshot() -> dict:
    """Synchronous REST snapshot fetch. Keep a small interval to avoid rate limits."""
    try:
        r = requests.get(BINANCE_REST_DEPTH, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.exception("Error fetching orderbook snapshot")
        return {"bids": [], "asks": []}

def fetch_mid_price() -> float:
    """Get the current mid-price using ticker or best bid/ask"""
    try:
        r = requests.get(BINANCE_REST_TICKER, timeout=8)
        r.raise_for_status()
        price = float(r.json().get("price", 0.0))
        return price
    except Exception:
        # fallback: 0.0 (won't break but trades should use mid from aggTrade)
        return 0.0

# -----------------------
# Strategy: detect divergence -> signal fade
# -----------------------
async def signal_logic_loop(cvd_tracker: CVDTracker):
    """
    Periodically compute OBI and CVD slope. Create signals on divergence.
    - If OBI > OBI_THRESHOLD (bids big) but CVD_slope < MIN_CVD_SLOPE -> OBI not supported -> FADE SHORT
    - If OBI < -OBI_THRESHOLD (asks big) but CVD_slope > -MIN_CVD_SLOPE -> FADE LONG
    """
    last_signal_time = 0
    signal_cooldown = 10  # seconds between signals to reduce spam; tune as needed

    while True:
        try:
            snapshot = fetch_orderbook_snapshot()
            obi = compute_obi_from_snapshot(snapshot, top_n=TOP_LEVELS)
            mid = await asyncio.get_event_loop().run_in_executor(None, fetch_mid_price)
            cvd_slope = await cvd_tracker.get_slope()

            log.info(f"OBI={obi:.4f} | CVD_slope={cvd_slope:.6f} | mid={mid:.2f}")

            now = time.time()
            if now - last_signal_time > signal_cooldown:
                # Condition A: strong bid imbalance but CVD doesn't follow -> fake push up -> fade short
                if obi >= OBI_THRESHOLD and cvd_slope < MIN_CVD_SLOPE:
                    msg = (f"*FADE SHORT SIGNAL* on `{SYMBOL}`\n"
                           f"OBI={obi:.3f} (bids > asks)\n"
                           f"CVD slope={cvd_slope:.6f} (no buying volume)\n"
                           f"Possible stop-run up — look for wick/rejection to short.\n"
                           f"TopLevels={TOP_LEVELS} | Interval={POLL_INTERVAL}s")
                    send_telegram(msg)
                    log.warning("FADE SHORT -> sent signal")
                    last_signal_time = now

                # Condition B: strong ask imbalance but CVD not selling -> fake push down -> fade long
                elif obi <= -OBI_THRESHOLD and cvd_slope > -MIN_CVD_SLOPE:
                    msg = (f"*FADE LONG SIGNAL* on `{SYMBOL}`\n"
                           f"OBI={obi:.3f} (asks > bids)\n"
                           f"CVD slope={cvd_slope:.6f} (no selling volume)\n"
                           f"Possible stop-run down — look for wick/rejection to long.\n"
                           f"TopLevels={TOP_LEVELS} | Interval={POLL_INTERVAL}s")
                    send_telegram(msg)
                    log.warning("FADE LONG -> sent signal")
                    last_signal_time = now

            await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            log.exception("Error in signal_logic_loop")
            await asyncio.sleep(2)

# -----------------------
# Websocket: aggTrade to update CVD
# -----------------------
async def aggtrade_listener(cvd_tracker: CVDTracker):
    """
    Connect to Binance aggTrade websocket for symbol and update CVD on each trade.
    Message format (simplified): {"p": "price","q":"qty", ...}
    """
    session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    reconnect_delay = 2
    while True:
        try:
            async with aiohttp.ClientSession(timeout=session_timeout) as session:
                async with session.ws_connect(BINANCE_WS, max_msg_size=0) as ws:
                    log.info(f"Connected to Binance aggTrade stream for {SYMBOL}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            # aggTrade fields: 'p' (price), 'q' (qty)
                            price = float(data.get("p", 0.0))
                            qty = float(data.get("q", 0.0))
                            # For mid price we will use recent REST ticker as rough mid; trade-level mid estimation would require best bid/ask
                            mid_price = await asyncio.get_event_loop().run_in_executor(None, fetch_mid_price)
                            await cvd_tracker.add_trade(price=price, qty=qty, mid_price=mid_price)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            log.error("WS error, reconnecting")
                            break
        except Exception as e:
            log.exception("aggtrade_listener connection error")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(30, reconnect_delay * 1.5)
        else:
            reconnect_delay = 2

# -----------------------
# Main
# -----------------------
async def main():
    send_telegram(f"OBI+CVD Fade Signal Bot starting for {SYMBOL} — PID:{os.getpid()}")
    cvd_tracker = CVDTracker(window_seconds=CVD_WINDOW)
    await asyncio.gather(
        aggtrade_listener(cvd_tracker),
        signal_logic_loop(cvd_tracker)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception:
        log.exception("Main crashed")
