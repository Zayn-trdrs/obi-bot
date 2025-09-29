# arbitrage_lowliquidity.py
# Multi-exchange arbitrage monitor focused on low-liquidity coins.
# ALERT-ONLY. Uses public REST endpoints. Secrets must be set in environment variables.

import os
import time
import math
import csv
import requests
from datetime import datetime
from time import sleep

# ---------------- CONFIG (tweak via Render env vars) ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))          # seconds between scans
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "300"))  # heartbeat to Telegram
DEPTH_LEVELS = int(os.getenv("DEPTH_LEVELS", "6"))             # top N depth levels to fetch per exchange
REPORT_LIMIT = int(os.getenv("REPORT_LIMIT", "30"))           # max symbols in one alert message

# Opportunitiy filters
MIN_SPREAD_PCT = float(os.getenv("MIN_SPREAD_PCT", "1.0"))    # minimum spread percent to alert (e.g., 1.0)
MIN_PROFIT_USDT = float(os.getenv("MIN_PROFIT_USDT", "5"))    # minimum estimated profit to alert (USDT)
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", "10"))  # minimum executable notional to consider
MAX_AVG_VOLUME_USDT = float(os.getenv("MAX_AVG_VOLUME_USDT", "50000"))  # define "low-liquidity" if avg 24h vol < this

CSV_LOG = os.getenv("CSV_LOG", "arb_lowliq_log.csv")

# Exchanges list (supported)
EXCHANGES = os.getenv("EXCHANGES", "binance,kucoin,mexc,bitget,okx").split(",")

# Watchlist (optional). If empty, the script will use a default list and then filter by low liquidity.
WATCHLIST = os.getenv("WATCHLIST", "")  # e.g., "SOLUSDT,AVAXUSDT" or leave empty

# HTTP settings
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "8"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "1.6"))

# --------------------------------------------------------------------

# ----------------- helpers -----------------
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID; cannot send Telegram messages.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

def http_get(url, params=None, headers=None, timeout=REQUEST_TIMEOUT):
    attempt = 0
    wait = 0.6
    while attempt < MAX_RETRIES:
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            time.sleep(wait)
            wait *= RETRY_BACKOFF
    return None

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

# ----------------- exchange adapters -----------------
# Each adapter returns:
#   tickers_map: {symbol: {"bid": float, "ask": float, "vol24": float (USDT approx if available)}} OR None on fail
#   depth_fetcher(symbol) -> (bids, asks) lists of (price, qty) limited by DEPTH_LEVELS or (None, None)

def fetch_binance_ticker(symbol=None):
    # If symbol provided, call /ticker/bookTicker (single). For many symbols, use exchangeInfo + 24hr tickers (not used here).
    if symbol:
        url = "https://api.binance.com/api/v3/ticker/bookTicker"
        data = http_get(url, params={"symbol": symbol})
        if not data: return None
        try:
            return {symbol: {"bid": float(data["bidPrice"]), "ask": float(data["askPrice"])}}
        except:
            return None
    return None

def fetch_binance_depth(symbol):
    url = "https://api.binance.com/api/v3/depth"
    data = http_get(url, params={"symbol": symbol, "limit": max(DEPTH_LEVELS, 5)})
    if not data: return None, None
    bids = [(float(p), float(q)) for p,q in data.get("bids", [])][:DEPTH_LEVELS]
    asks = [(float(p), float(q)) for p,q in data.get("asks", [])][:DEPTH_LEVELS]
    return bids, asks

def fetch_binance_24h(symbol):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    data = http_get(url, params={"symbol": symbol})
    if not data: return None
    try:
        # quoteVolume is in quote asset (USDT)
        vol = float(data.get("quoteVolume", 0.0))
        return vol
    except:
        return None

def fetch_kucoin_alltickers():
    url = "https://api.kucoin.com/api/v1/market/allTickers"
    data = http_get(url)
    if not data or "data" not in data: return None
    tmap = {}
    for t in data["data"].get("ticker", []):
        sym = t.get("symbol","").replace("-","")
        if sym.endswith("USDT"):
            try:
                tmap[sym] = {"bid": float(t.get("buy")), "ask": float(t.get("sell")), "vol24": float(t.get("volUsd", 0)) if t.get("volUsd") else None}
            except:
                continue
    return tmap

def fetch_kucoin_depth(symbol):
    url = "https://api.kucoin.com/api/v1/market/orderbook/level2"
    data = http_get(url, params={"symbol": symbol})
    if not data or "data" not in data: return None, None
    bids = [(float(p), float(q)) for p,q in data["data"].get("bids", [])][:DEPTH_LEVELS]
    asks = [(float(p), float(q)) for p,q in data["data"].get("asks", [])][:DEPTH_LEVELS]
    return bids, asks

def fetch_mexc_ticker(symbol=None):
    if symbol:
        url = "https://www.mexc.com/api/v3/ticker/bookTicker"
        data = http_get(url, params={"symbol": symbol})
        if not data: return None
        try:
            return {symbol: {"bid": float(data["bidPrice"]), "ask": float(data["askPrice"])}}
        except:
            return None
    return None

def fetch_mexc_depth(symbol):
    url = "https://www.mexc.com/api/v2/market/depth"
    data = http_get(url, params={"symbol": symbol, "depth": DEPTH_LEVELS})
    if not data or "data" not in data: return None, None
    d = data["data"]
    bids = [(float(p), float(q)) for p,q in d.get("bids", [])][:DEPTH_LEVELS]
    asks = [(float(p), float(q)) for p,q in d.get("asks", [])][:DEPTH_LEVELS]
    return bids, asks

def fetch_bitget_tickers():
    url = "https://api.bitget.com/api/spot/v1/market/tickers"
    data = http_get(url)
    if not data or "data" not in data: return None
    tmap = {}
    for it in data["data"]:
        sym = it.get("symbol","").replace("_","")
        if sym.endswith("USDT"):
            try:
                tmap[sym] = {"bid": float(it.get("buyOne")), "ask": float(it.get("sellOne"))}
            except:
                continue
    return tmap

def fetch_bitget_depth(symbol):
    url = "https://api.bitget.com/api/spot/v1/market/depth"
    data = http_get(url, params={"symbol": symbol})
    if not data or "data" not in data: return None, None
    d = data["data"]
    bids = [(float(p), float(q)) for p,q in d.get("bids", [])][:DEPTH_LEVELS]
    asks = [(float(p), float(q)) for p,q in d.get("asks", [])][:DEPTH_LEVELS]
    return bids, asks

def fetch_okx_tickers():
    url = "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
    data = http_get(url)
    if not data or "data" not in data: return None
    tmap = {}
    for it in data["data"]:
        inst = it.get("instId","").replace("-","")
        if inst.endswith("USDT"):
            try:
                tmap[inst] = {"bid": float(it.get("bidPx")), "ask": float(it.get("askPx"))}
            except:
                continue
    return tmap

def fetch_okx_depth(symbol):
    url = "https://www.okx.com/api/v5/market/books"
    data = http_get(url, params={"instId": symbol, "sz": DEPTH_LEVELS})
    if not data or "data" not in data: return None, None
    d = data["data"][0]
    bids = [(float(p[0]), float(p[1])) for p in d.get("bids", [])][:DEPTH_LEVELS]
    asks = [(float(p[0]), float(p[1])) for p in d.get("asks", [])][:DEPTH_LEVELS]
    return bids, asks

# mapping
TICKER_FETCHERS = {
    "binance": lambda sy=None: fetch_binance_ticker(sy) if sy else None,
    "kucoin": lambda sy=None: fetch_kucoin_alltickers(),
    "mexc": lambda sy=None: fetch_mexc_ticker(sy) if sy else None,
    "bitget": lambda sy=None: fetch_bitget_tickers(),
    "okx": lambda sy=None: fetch_okx_tickers()
}
DEPTH_FETCHERS = {
    "binance": fetch_binance_depth,
    "kucoin": fetch_kucoin_depth,
    "mexc": fetch_mexc_depth,
    "bitget": fetch_bitget_depth,
    "okx": fetch_okx_depth
}
VOL_FETCHERS = {
    "binance": fetch_binance_24h,
    # kucoin VOL via allTickers may include volUsd but not reliable; skip per-exchange 24h as needed
}

# ----------------- utility: estimate buy/sell from limited depth -----------------
def estimate_buy_from_asks(asks, target_notional):
    """spend up to target_notional USDT buying base across asks"""
    remain = target_notional
    acquired = 0.0
    spent = 0.0
    levels_used = []
    for price, qty in asks:
        level_notional = price * qty
        if remain <= 0:
            break
        use = min(level_notional, remain)
        qty_used = use / price
        acquired += qty_used
        spent += use
        remain -= use
        levels_used.append((price, qty_used, use))
    return spent, acquired, (spent / acquired) if acquired>0 else None, levels_used

def estimate_sell_on_bids(bids, target_base_qty):
    """sell up to target_base_qty base across bids"""
    remain = target_base_qty
    received = 0.0
    sold = 0.0
    levels_used = []
    for price, qty in bids:
        if remain <= 0:
            break
        use_qty = min(qty, remain)
        notional = use_qty * price
        received += notional
        sold += use_qty
        remain -= use_qty
        levels_used.append((price, use_qty, notional))
    return received, sold, (received / sold) if sold>0 else None, levels_used

# find best buy->sell executable notional and profit (simple candidate scanning)
def evaluate_pair(buy_asks, sell_bids, fee_buy=0.001, fee_sell=0.001, capital_limit=100.0):
    # build candidate notional levels based on ask/bid level notionals and fractions of capital
    candidate_notions = set()
    for p,q in buy_asks[:DEPTH_LEVELS]:
        candidate_notions.add(round(p*q,2))
    for p,q in sell_bids[:DEPTH_LEVELS]:
        candidate_notions.add(round(p*q,2))
    candidate_notions.update([round(capital_limit * f,2) for f in (0.05,0.1,0.25,0.5,1.0)])
    cand = sorted([c for c in candidate_notions if c>1.0])
    best = {"notional":0,"profit":-1e9,"profit_pct":-999.0,"buy_exec":None,"sell_exec":None}
    for notional in cand:
        if notional > capital_limit + 1e-9:
            continue
        buy_spent, base_acq, buy_avg, buy_lv = estimate_buy_from_asks(buy_asks, notional)
        if base_acq <= 0: continue
        sell_recv, base_sold, sell_avg, sell_lv = estimate_sell_on_bids(sell_bids, base_acq)
        if base_sold <= 0: continue
        cost_with_fee = buy_spent * (1+fee_buy)
        recv_after_fee = sell_recv * (1-fee_sell)
        profit = recv_after_fee - cost_with_fee
        profit_pct = (profit / cost_with_fee * 100.0) if cost_with_fee>0 else 0.0
        if profit > best["profit"] and profit > 0:
            best.update({"notional": buy_spent, "profit": profit, "profit_pct": profit_pct, "buy_exec":(buy_spent, base_acq, buy_avg, buy_lv), "sell_exec":(sell_recv, base_sold, sell_avg, sell_lv)})
    return best

# ----------------- CSV logging -----------------
def log_to_csv(row):
    header = ["timestamp","symbol","buy_ex","sell_ex","notional","profit","profit_pct","buy_levels","sell_levels"]
    exists = os.path.exists(CSV_LOG)
    with open(CSV_LOG,"a",newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(header)
        w.writerow(row)

# ----------------- main loop -----------------
def run():
    symbols = []
    if WATCHLIST:
        symbols = [s.strip().upper().replace("/","").replace("-","") for s in WATCHLIST.split(",") if s.strip()]
    else:
        # default set - can be extended
        symbols = ["SOLUSDT","AVAXUSDT","FTMUSDT","MANAUSDT","SANDUSDT","RUNEUSDT","XLMUSDT","TRXUSDT","MATICUSDT","LRCUSDT"]
    last_hb = 0
    send_telegram(f"🔔 Low-liquidity arbitrage bot started. Scanning {len(symbols)} symbols on {','.join(EXCHANGES)}")
    while True:
        try:
            # 1) gather top-of-book tickers per exchange for our symbols
            topo = {}  # ex -> {sym: (bid, ask)}
            for ex in EXCHANGES:
                ex = ex.strip().lower()
                try:
                    if ex == "binance":
                        ex_map = {}
                        for s in symbols:
                            d = fetch_binance_ticker(s)
                            if d and s in d:
                                ex_map[s] = (d[s]["bid"], d[s]["ask"])
                        if ex_map: topo["binance"] = ex_map
                    elif ex == "kucoin":
                        allt = fetch_kucoin_alltickers()
                        if allt:
                            ex_map={}
                            for s in symbols:
                                if s in allt:
                                    ex_map[s] = (allt[s]["bid"], allt[s]["ask"])
                            if ex_map: topo["kucoin"] = ex_map
                    elif ex == "mexc":
                        ex_map={}
                        for s in symbols:
                            d = fetch_mexc_ticker(s)
                            if d and s in d:
                                ex_map[s] = (d[s]["bid"], d[s]["ask"])
                        if ex_map: topo["mexc"] = ex_map
                    elif ex == "bitget":
                        d = fetch_bitget_tickers()
                        if d:
                            ex_map={}
                            for s in symbols:
                                if s in d:
                                    ex_map[s] = (d[s]["bid"], d[s]["ask"])
                            if ex_map: topo["bitget"] = ex_map
                    elif ex == "okx":
                        d = fetch_okx_tickers()
                        if d:
                            ex_map={}
                            for s in symbols:
                                if s in d:
                                    ex_map[s] = (d[s]["bid"], d[s]["ask"])
                            if ex_map: topo["okx"] = ex_map
                except Exception as e:
                    send_telegram(f"⚠️ Adapter error {ex}: {e}")
                    continue

            # 2) optionally compute per-symbol avg 24h vol (if available) and filter to low-liquidity
            lowliq_symbols = []
            for s in symbols:
                avg_vol = None
                # try binance 24h for approximate volume
                if "binance" in EXCHANGES:
                    vol = fetch_binance_24h(s)
                    if vol:
                        avg_vol = vol
                # if avg_vol present and <= threshold, keep; if avg_vol missing, keep symbol (we want low-liq candidates)
                if avg_vol is None or avg_vol <= MAX_AVG_VOLUME_USDT:
                    lowliq_symbols.append(s)
            # use lowliq_symbols for further checks
            check_symbols = lowliq_symbols

            # 3) compute spreads: for each symbol find best ask (buy) and best bid (sell) across exchanges
            spreads = []
            for s in check_symbols:
                best_ask = 1e18; best_ask_ex = None
                best_bid = -1.0; best_bid_ex = None
                for ex, market in topo.items():
                    if s in market:
                        bid, ask = market[s]
                        if ask and ask < best_ask:
                            best_ask = ask; best_ask_ex = ex
                        if bid and bid > best_bid:
                            best_bid = bid; best_bid_ex = ex
                if best_ask_ex and best_bid_ex and best_ask_ex != best_bid_ex:
                    spread_pct = (best_bid - best_ask) / best_ask * 100.0
                    spreads.append((s, best_ask, best_ask_ex, best_bid, best_bid_ex, spread_pct))

            # 4) evaluate executable notional & profit using depth fetchers for top candidates
            alerts = []
            for s, a_price, a_ex, b_price, b_ex, spc in sorted(spreads, key=lambda x: x[5], reverse=True)[:REPORT_LIMIT]:
                # quick skip if below minimum spread percent
                if spc < MIN_SPREAD_PCT:
                    continue
                # fetch depth for both sides
                buy_depth = None; sell_depth = None
                if a_ex in DEPTH_FETCHERS:
                    buy_depth = DEPTH_FETCHERS[a_ex](s)
                if b_ex in DEPTH_FETCHERS:
                    sell_depth = DEPTH_FETCHERS[b_ex](s)
                # if either depth missing skip
                if not buy_depth or not sell_depth:
                    continue
                buy_asks = buy_depth[1]  # asks returned as (asks) for buy exchange in some adapters; check adapter returns
                sell_bids = sell_depth[0]
                # normalize if adapter returns reversed order (we assume adapters above match signature)
                # evaluate best executable notional and profit (assume conservative taker fees 0.002)
                fee_buy = 0.002; fee_sell = 0.002
                best = evaluate_pair(buy_asks, sell_bids, fee_buy, fee_sell, capital_limit=float(MIN_NOTIONAL_USDT*10 if MIN_NOTIONAL_USDT*10>MIN_NOTIONAL_USDT else MIN_NOTIONAL_USDT))
                if best["profit"] > 0 and best["notional"] >= MIN_NOTIONAL_USDT and best["profit"] >= MIN_PROFIT_USDT:
                    alerts.append((s, a_ex, b_ex, spc, best))
            # 5) send alerts if any
            if alerts:
                lines = []
                lines.append(f"🚨 Low-liq Arb Alerts {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
                for s, a_ex, b_ex, spc, best in alerts:
                    buy_exec = best["buy_exec"]; sell_exec = best["sell_exec"]
                    lines.append(f"{s}: Buy {a_ex} Sell {b_ex} | Spread {spc:+.3f}% | Notional {best['notional']:.2f} USDT | Profit {best['profit']:.2f} USDT ({best['profit_pct']:.2f}%)")
                    lines.append(f"  buy_levels: {[(round(p,6), round(q,6), round(n,2)) for p,q,n in buy_exec[3]]}")
                    lines.append(f"  sell_levels: {[(round(p,6), round(q,6), round(n,2)) for p,q,n in sell_exec[3]]}")
                    # log
                    log_to_csv([datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), s, a_ex, b_ex, round(best["notional"],2), round(best["profit"],2), round(best["profit_pct"],4), str(buy_exec[3]), str(sell_exec[3])])
                send_telegram("\n".join(lines[:4000]))
            else:
                # optionally send no-alert message? skip to avoid spam
                pass

            # heartbeat
            if time.time() - last_hb > HEARTBEAT_INTERVAL:
                send_telegram("✅ Low-liq arb bot heartbeat: running.")
                last_hb = time.time()

        except Exception as e:
            # send error once and continue
            try:
                send_telegram(f"❗ Bot runtime error: {e}")
            except:
                print("Error sending error message:", e)
        # sleep between cycles
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    run()
