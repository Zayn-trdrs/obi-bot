""" Advanced Tape-Reading Signal Bot -> sends Telegram alerts

What this script does (summary):

Connects to Binance public websocket for trades and order book snapshots

Builds a lightweight order-flow / tape-reading engine (imbalance, absorption, exhaustion, stop-run)

Emits high-confidence signals to a Telegram chat via the bot API


IMPORTANT:

This is a production-ready starting point but you MUST test with paper/dry-run first.

Fill TELEGRAM_TOKEN and TELEGRAM_CHAT_ID before running.

Tune parameters to the pair/timeframe you trade.


Dependencies: pip install websockets aiohttp

Run: python3 telegram_tape_bot.py

Limitations & notes:

Runs on Binance public streams; uses no private API keys.

For lower latency or exchange-specific features (user data, orders) use exchange SDKs.

This code is intentionally "exchange-agnostic" in the sense it uses public websockets.


"""

import asyncio import json import math import time from collections import deque, defaultdict from dataclasses import dataclass from typing import Deque, Dict, List

import aiohttp import websockets

--------------------- USER CONFIG ---------------------

SYMBOL = "btcusdt"                # lowercase symbol for Binance websocket TELEGRAM_TOKEN = "REPLACE_WITH_YOUR_TELEGRAM_BOT_TOKEN" TELEGRAM_CHAT_ID = "REPLACE_WITH_YOUR_CHAT_ID"

Sensitivity / thresholds

TRADE_WINDOW_SECONDS = 3          # rolling window for tape analysis IMBALANCE_THRESHOLD = 0.7        # fraction of aggressive buys (0..1) to trigger momentum LARGE_TRADE_SIZE = 0.5           # in base asset (e.g., BTC) considered large — tune per symbol ABSORPTION_MULTIPLIER = 8        # how many small trades consumed by large resting liquidity to count as absorption STOPRUN_PRICE_MOVE = 0.003      # 0.3% quick sweep then reversal threshold MIN_COOLDOWN = 5                 # seconds between signals for the same side

Safety / execution

DRY_RUN = False                   # set True to avoid sending Telegram messages while testing LOG_EVERY = 60                    # seconds to print summary

-------------------------------------------------------

@dataclass class Trade: timestamp: float price: float qty: float is_buyer_maker: bool  # True => trade executed on the maker side (i.e., seller aggressor)

class TapeEngine: """Simple order-flow/tape engine collecting trades & book snapshots and emitting signals."""

def __init__(self):
    self.trades: Deque[Trade] = deque()
    self.last_signal_time = {"long": 0.0, "short": 0.0}
    self.last_mid = None
    self.snapshots = deque(maxlen=50)

def add_trade(self, price: float, qty: float, is_buyer_maker: bool):
    t = Trade(time.time(), float(price), float(qty), bool(is_buyer_maker))
    self.trades.append(t)
    # prune
    cutoff = time.time() - TRADE_WINDOW_SECONDS
    while self.trades and self.trades[0].timestamp < cutoff:
        self.trades.popleft()

def add_snapshot(self, bid: float, ask: float, bid_size: float, ask_size: float):
    mid = (bid + ask) / 2
    self.snapshots.append((time.time(), bid, ask, bid_size, ask_size, mid))
    self.last_mid = mid

def calc_imbalance(self):
    if not self.trades:
        return 0.5
    buy_volume = 0.0  # aggressive buys (taker buys -> is_buyer_maker == False)
    sell_volume = 0.0
    for tr in self.trades:
        if tr.is_buyer_maker:
            # buyer is maker -> seller was aggressor -> executed against bid -> aggressive sell
            sell_volume += tr.qty * tr.price
        else:
            buy_volume += tr.qty * tr.price
    total = buy_volume + sell_volume
    if total == 0:
        return 0.5
    return buy_volume / total

def detect_large_trades(self):
    # Count number of "large" trades in window and their side
    large_buys = 0
    large_sells = 0
    for tr in self.trades:
        if tr.qty >= LARGE_TRADE_SIZE:
            if tr.is_buyer_maker:
                large_sells += 1
            else:
                large_buys += 1
    return large_buys, large_sells

def detect_absorption(self):
    """Simple absorption detector: many small aggressive trades hitting a level while book shows heavy resting opposite size.
    We approximate using snapshots + trades. This is a heuristic, not perfect — tune it.
    """
    if not self.snapshots:
        return None
    # Look at latest snapshot and trades
    ts, bid, ask, bid_size, ask_size, mid = self.snapshots[-1]
    # If many aggressive sells (hitting bids) but bid_size remains high => absorption (buy-side absorbing)
    sell_hits = 0
    buy_hits = 0
    small_sell_total = 0.0
    small_buy_total = 0.0
    for tr in self.trades:
        if tr.is_buyer_maker:
            sell_hits += 1
            small_sell_total += tr.qty
        else:
            buy_hits += 1
            small_buy_total += tr.qty
    # Heuristic: if sell hits >> buy hits but bid_size not dropping (still large), it's absorption
    if sell_hits >= 2 and buy_hits <= 1:
        # check ratio between resting bid size vs recent sells
        if bid_size >= (small_sell_total * ABSORPTION_MULTIPLIER):
            return "absorb_buy"  # bullish absorption
    if buy_hits >= 2 and sell_hits <= 1:
        if ask_size >= (small_buy_total * ABSORPTION_MULTIPLIER):
            return "absorb_sell"
    return None

def detect_stoprun(self):
    # Detect a fast sweep beyond recent mid then immediate reversal
    if len(self.snapshots) < 6:
        return None
    latest = self.snapshots[-1]
    prev = self.snapshots[-6]
    # percent move
    mid_move = (latest[5] - prev[5]) / prev[5]
    if abs(mid_move) >= STOPRUN_PRICE_MOVE:
        # direction of sweep
        direction = "long_sweep" if mid_move > 0 else "short_sweep"
        # If after sweep, the mid returns inside previous range -> possible stop-run
        # Quick check: compare mid after 1-2 snapshots
        for s in list(self.snapshots)[-4:]:
            if direction == "long_sweep" and s[5] < prev[5] * (1 + STOPRUN_PRICE_MOVE / 2):
                return "stoprun_short"
            if direction == "short_sweep" and s[5] > prev[5] * (1 - STOPRUN_PRICE_MOVE / 2):
                return "stoprun_long"
    return None

def analyze(self):
    signals = []
    imbalance = self.calc_imbalance()
    large_buys, large_sells = self.detect_large_trades()
    absr = self.detect_absorption()
    stoprun = self.detect_stoprun()

    # Imbalance momentum signal
    if imbalance >= IMBALANCE_THRESHOLD and large_buys >= 1:
        signals.append(("long", f"imbalance_buy {imbalance:.2f} large_buys={large_buys}"))
    if imbalance <= (1 - IMBALANCE_THRESHOLD) and large_sells >= 1:
        signals.append(("short", f"imbalance_sell {imbalance:.2f} large_sells={large_sells}"))

    # Absorption
    if absr == "absorb_buy":
        signals.append(("long", "absorption_buy_detected"))
    if absr == "absorb_sell":
        signals.append(("short", "absorption_sell_detected"))

    # Stop-run
    if stoprun == "stoprun_long":
        signals.append(("long", "stoprun_long (sweep & reverse)"))
    if stoprun == "stoprun_short":
        signals.append(("short", "stoprun_short (sweep & reverse)"))

    # Reduce duplicate/conflicting signals: prefer latest unique side
    final = {}
    for side, text in signals:
        final[side] = text

    # apply cooldown
    now = time.time()
    out = []
    for side, text in final.items():
        if now - self.last_signal_time.get(side, 0) > MIN_COOLDOWN:
            out.append((side, text))
            self.last_signal_time[side] = now
    return out

---------------- Telegram helper ----------------

async def send_telegram(message: str): if DRY_RUN: print("[DRY-RUN] Telegram message would be:\n", message) return if TELEGRAM_TOKEN.startswith("REPLACE") or TELEGRAM_CHAT_ID.startswith("REPLACE"): print("Telegram token/chat-id not set. Skipping send. Message:\n", message) return url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage" payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message} async with aiohttp.ClientSession() as session: try: async with session.post(url, json=payload, timeout=10) as resp: text = await resp.text() if resp.status != 200: print("Telegram send failed:", resp.status, text) except Exception as e: print("Telegram send exception:", e)

--------------- Binance websocket handling ---------------

BINANCE_WS_BASE = "wss://stream.binance.com:9443/stream?streams="

async def stream_handler(symbol: str, engine: TapeEngine): # build combined streams: trade (aggTrade) and depth (bookTicker for top-of-book) trade_stream = f"{symbol}@trade"  # trade provides isBuyerMaker book_stream = f"{symbol}@bookTicker"  # top of book snapshot url = BINANCE_WS_BASE + trade_stream + "/" + book_stream print("Connecting to", url) backoff = 1 while True: try: async with websockets.connect(url, max_size=2**25) as ws: print("Connected to websocket") backoff = 1 async for raw in ws: data = json.loads(raw) # combined stream has 'stream' and 'data' if "data" not in data: continue payload = data["data"] stream = data.get("stream", "") if payload.get("e") == "trade": # sample payload fields: p=price, q=qty, m=isBuyerMaker price = float(payload["p"]) qty = float(payload["q"]) is_buyer_maker = bool(payload["m"])  # True => buyer is maker => seller aggressor engine.add_trade(price, qty, is_buyer_maker) elif payload.get("e") == "bookTicker": bid = float(payload["b"])  # best bid price ask = float(payload["a"])  # best ask # we don't get sizes in bookTicker; use a snapshot poll to fetch sizes occasionally engine.add_snapshot(bid, ask, bid_size=0.0, ask_size=0.0) # analyze sigs = engine.analyze() for side, txt in sigs: msg = f"[{symbol.upper()}] {side.upper()} signal: {txt} | mid={engine.last_mid} | imbalance={engine.calc_imbalance():.2f}" print(msg) # recommend SL/TP based on recent mid & average trade size (VERY basic) sl = engine.last_mid * (0.999 if side == 'long' else 1.001) tp = engine.last_mid * (1.003 if side == 'long' else 0.997) msg += f"\nSuggested: SL={sl:.2f} TP={tp:.2f}" asyncio.create_task(send_telegram(msg)) except Exception as e: print(f"Websocket error: {e}. reconnecting in {backoff}s") await asyncio.sleep(backoff) backoff = min(backoff * 2, 30)

---------------- Main ----------------

async def periodic_book_size_fetch(symbol: str, engine: TapeEngine): # bookTicker doesn't include sizes for both sides; poll REST order book occasionally to get top size url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit=5" async with aiohttp.ClientSession() as session: while True: try: async with session.get(url, timeout=5) as resp: if resp.status == 200: j = await resp.json() bid = float(j["bids"][0][0]) bid_size = float(j["bids"][0][1]) ask = float(j["asks"][0][0]) ask_size = float(j["asks"][0][1]) engine.add_snapshot(bid, ask, bid_size, ask_size) except Exception as e: print("book fetch error:", e) await asyncio.sleep(1.0)

async def reporter(engine: TapeEngine): while True: await asyncio.sleep(LOG_EVERY) imbalance = engine.calc_imbalance() lb, ls = engine.detect_large_trades() print(f"REPORT: trades_in_window={len(engine.trades)} imbalance={imbalance:.2f} large_buys={lb} large_sells={ls}")

async def main(): engine = TapeEngine() tasks = [stream_handler(SYMBOL, engine), periodic_book_size_fetch(SYMBOL, engine), reporter(engine)] await asyncio.gather(*tasks)

if name == 'main': try: asyncio.run(main()) except KeyboardInterrupt: print("Exiting")

