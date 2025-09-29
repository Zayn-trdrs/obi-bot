# safe_arbitrage_bot.py
# Robust multi-exchange arbitrage scanner (alert-only).
# DO NOT hardcode secrets. Use env vars in Render: TELEGRAM_TOKEN, CHAT_ID

import os
import time
import csv
import math
import json
import requests
from datetime import datetime
from time import sleep

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")        # set in Render
CHAT_ID = os.getenv("CHAT_ID")                      # set in Render

# polling & heartbeat
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "300"))  # 5 minutes

# depth levels to fetch (per exchange adapter)
DEPTH_LEVELS = int(os.getenv("DEPTH_LEVELS", "5"))

# which exchanges to call (supported adapters below)
EXCHANGES = os.getenv("EXCHANGES", "binance,kucoin,mexc,bitget,okx").split(",")

# how many symbols to report per cycle (limit to avoid giant messages)
REPORT_SYMBOL_LIMIT = int(os.getenv("REPORT_SYMBOL_LIMIT", "60"))

# CSV log file for alerts
CSV_LOG = os.getenv("CSV_LOG", "arbitrage_spreads.csv")

# User-supplied watchlist or default top pairs
WATCHLIST = os.getenv("WATCHLIST", "")  # comma-separated like "ETHUSDT,BTCUSDT"
DEFAULT_SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","MATICUSDT","LTCUSDT","ADAUSDT","AVAXUSDT"]

# HTTP settings
REQUEST_TIMEOUT = 8
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # multiplier

# ---------- END CONFIG ----------

# ---------- Helper: Telegram ----------
def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID; cannot send Telegram messages.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
        # optional: check response
        if resp.status_code != 200:
            print("Telegram send failed:", resp.status_code, resp.text)
    except Exception as e:
        print("Telegram error:", e)

# ---------- Helper: simple request with retries ----------
def http_get(url, params=None, headers=None, timeout=REQUEST_TIMEOUT):
    attempt = 0
    wait = 0.8
    while attempt < MAX_RETRIES:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            sleep(wait)
            wait *= RETRY_BACKOFF
    return None

# ---------- Exchange adapters (public endpoints) ----------
def fetch_binance_booktick(symbol):
    # symbol: "ETHUSDT"
    url = "https://api.binance.com/api/v3/ticker/bookTicker"
    data = http_get(url, params={"symbol": symbol})
    if not data:
        return None
    # bookTicker returns one symbol if symbol param provided
    if isinstance(data, dict) and "bidPrice" in data:
        try:
            bid = float(data["bidPrice"])
            ask = float(data["askPrice"])
            return {"bid": bid, "ask": ask}
        except:
            return None
    return None

def fetch_binance_symbols():
    url = "https://api.binance.com/api/v3/exchangeInfo"
    d = http_get(url)
    if not d: return []
    symbols = [s["symbol"] for s in d.get("symbols", []) if s.get("quoteAsset") == "USDT" and s.get("status")=="TRADING"]
    return symbols

def fetch_kucoin_all_tickers():
    url = "https://api.kucoin.com/api/v1/market/allTickers"
    d = http_get(url)
    if not d or "data" not in d: return None
    # returns data.ticker list
    tickers = {}
    for t in d["data"].get("ticker", []):
        sym = t.get("symbol","").replace("-","")
        if sym.endswith("USDT"):
            try:
                bid = float(t.get("buy"))
                ask = float(t.get("sell"))
                tickers[sym] = {"bid":bid,"ask":ask}
            except:
                continue
    return tickers

def fetch_mexc_booktick(symbol):
    url = "https://www.mexc.com/api/v3/ticker/bookTicker"
    d = http_get(url, params={"symbol": symbol})
    if not d: return None
    # d likely single dict or list; handle dict
    if isinstance(d, dict) and "symbol" in d:
        try:
            return {"bid": float(d["bidPrice"]), "ask": float(d["askPrice"])}
        except:
            return None
    return None

def fetch_bitget_tickers():
    url = "https://api.bitget.com/api/spot/v1/market/tickers"
    d = http_get(url)
    if not d or "data" not in d: return None
    tickers = {}
    for it in d["data"]:
        sym = it.get("symbol","").replace("_","")
        if sym.endswith("USDT"):
            try:
                bid = float(it.get("buyOne"))
                ask = float(it.get("sellOne"))
                tickers[sym] = {"bid":bid,"ask":ask}
            except:
                continue
    return tickers

def fetch_okx_tickers():
    url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    d = http_get(url)
    if not d or "data" not in d: return None
    tickers = {}
    for it in d["data"]:
        inst = it.get("instId","").replace("-","")
        if inst.endswith("USDT"):
            try:
                bid = float(it.get("bidPx"))
                ask = float(it.get("askPx"))
                tickers[inst] = {"bid":bid,"ask":ask}
            except:
                continue
    return tickers

# Map adapter name -> fetcher function for market tickers
ADAPTER_TICKERS = {
    "binance": lambda: {"single": fetch_binance_booktick},  # special-case single symbol fetcher below
    "kucoin": fetch_kucoin_all_tickers,
    "mexc": lambda: None,  # we'll call binance-like endpoint per symbol (handled if needed)
    "bitget": fetch_bitget_tickers,
    "okx": fetch_okx_tickers
}

# ---------- Utility: build symbol list ----------
def get_symbol_list():
    if WATCHLIST:
        return [s.strip().upper().replace("/","").replace("-","") for s in WATCHLIST.split(",") if s.strip()]
    # otherwise default set
    return DEFAULT_SYMBOLS

# ---------- Core scan logic (gather top-of-book across exchanges) ----------
def gather_top_of_book(symbols):
    """
    Return: dict mapping exchange -> {symbol: (bid, ask)}
    Missing or failed exchanges will be skipped but program continues.
    """
    results = {}
    # for binance and mexc we prefer per-symbol bookTicker for reliability
    for ex in EXCHANGES:
        ex = ex.strip().lower()
        try:
            if ex == "binance":
                # call per symbol
                ex_map = {}
                for s in symbols:
                    data = fetch_binance_booktick(s)
                    if data: ex_map[s] = (data["bid"], data["ask"])
                if ex_map: results["binance"] = ex_map
            elif ex == "kucoin":
                d = fetch_kucoin_all_tickers()
                if d: 
                    ex_map={}
                    for s in symbols:
                        if s in d: ex_map[s] = (d[s]["bid"], d[s]["ask"])
                    if ex_map: results["kucoin"] = ex_map
            elif ex == "mexc":
                # use bookTicker endpoint per symbol if available:
                ex_map={}
                for s in symbols:
                    d = fetch_mexc_booktick(s)
                    if d: ex_map[s] = (d["bid"], d["ask"])
                if ex_map: results["mexc"] = ex_map
            elif ex == "bitget":
                d = fetch_bitget_tickers()
                if d:
                    ex_map={}
                    for s in symbols:
                        if s in d: ex_map[s] = (d[s]["bid"], d[s]["ask"])
                    if ex_map: results["bitget"] = ex_map
            elif ex == "okx":
                d = fetch_okx_tickers()
                if d:
                    ex_map={}
                    for s in symbols:
                        if s in d: ex_map[s] = (d[s]["bid"], d[s]["ask"])
                    if ex_map: results["okx"] = ex_map
        except Exception as e:
            # log and continue
            send_telegram_message(f"⚠️ Adapter error for {ex}: {e}")
            continue
    return results

# ---------- Compute spreads across exchanges ----------
def compute_spreads(topo_map, symbols):
    spreads = []  # list of (symbol, best_ask, ask_ex, best_bid, bid_ex, spread_pct)
    for s in symbols:
        best_bid = -1.0; best_bid_ex = None
        best_ask = 1e18; best_ask_ex = None
        for ex, market_map in topo_map.items():
            if s in market_map:
                bid, ask = market_map[s]
                if bid is not None and bid > best_bid:
                    best_bid = bid; best_bid_ex = ex
                if ask is not None and ask < best_ask:
                    best_ask = ask; best_ask_ex = ex
        if best_bid_ex and best_ask_ex and best_bid_ex != best_ask_ex:
            spread_pct = (best_bid - best_ask) / best_ask * 100.0
            spreads.append((s, best_ask, best_ask_ex, best_bid, best_bid_ex, spread_pct))
    # sort largest spreads first
    spreads.sort(key=lambda x: x[5], reverse=True)
    return spreads

# ---------- CSV logging ----------
def log_spreads(spreads):
    if not spreads: return
    header = ["timestamp","symbol","buy_ex","buy_price","sell_ex","sell_price","spread_pct"]
    exists = os.path.exists(CSV_LOG)
    with open(CSV_LOG,"a",newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        for s, buy_p, buy_ex, sell_p, sell_ex, spread in spreads:
            w.writerow([ts,s,buy_ex,buy_p,sell_ex,sell_p,round(spread,6)])

# ---------- Main loop ----------
def run():
    symbols = get_symbol_list()
    last_heartbeat = 0
    send_telegram_message(f"🔔 Arbitrage scanner starting. Exchanges: {', '.join(EXCHANGES)} Symbols: {', '.join(symbols)}")
    while True:
        try:
            topo = gather_top_of_book(symbols)
            spreads = compute_spreads(topo, symbols)
            # log to csv
            log_spreads(spreads)
            # send message (limit large number)
            if spreads:
                # build text batches under 4000 char Telegram limit
                lines = []
                lines.append(f"🔍 Arbitrage scan {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
                count = 0
                for s, buy_p, buy_ex, sell_p, sell_ex, spr in spreads:
                    lines.append(f"{s}: Buy {buy_ex} @ {buy_p:.6f} → Sell {sell_ex} @ {sell_p:.6f} | Spread {spr:+.3f}%")
                    count += 1
                    if count >= REPORT_SYMBOL_LIMIT: break
                text = "\n".join(lines)
                send_telegram_message(text)
            else:
                # optionally send "no spreads" or skip to avoid spam
                pass

            # heartbeat every HEARTBEAT_INTERVAL
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                send_telegram_message("✅ Bot heartbeat: scanning exchanges...")
                last_heartbeat = time.time()

        except Exception as e:
            # catch all to keep running
            send_telegram_message(f"❗ Bot error: {e}")
            print("Main loop error:", e)

        sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    run()
