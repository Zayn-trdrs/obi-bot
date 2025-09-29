# arbitrage_bot.py
# Multi-exchange spot arbitrage alert monitor (Binance, KuCoin, MEXC, Bitget, OKX)
# ALERT-ONLY (no trading). Uses public REST APIs.
# Configure at top; run as a background worker (Render/Replit).

import os
import time
import csv
import math
import requests
from datetime import datetime

# ========== USER CONFIG ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")      # REQUIRED
CHAT_ID = os.getenv("CHAT_ID")                    # REQUIRED

CAPITAL_USDT = float(os.getenv("CAPITAL_USDT", "160"))  # Your available capital (USDT)
MIN_PROFIT_PCT = float(os.getenv("MIN_PROFIT_PCT", "0.6"))   # alert threshold percent
MIN_PROFIT_USDT = float(os.getenv("MIN_PROFIT_USDT", "5"))   # min absolute profit
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "45"))         # poll interval
DEPTH_LEVELS = int(os.getenv("DEPTH_LEVELS", "6"))          # top N levels to read
MAX_WALK_LEVELS = DEPTH_LEVELS                              # levels to walk to compute executable size

# Exchanges to include (supported: 'binance','kucoin','mexc','bitget','okx')
EXCHANGES = os.getenv("EXCHANGES", "binance,kucoin,mexc,bitget,okx").split(",")

# Fee assumptions (taker fees) - tune per your account level
TAKER_FEES = {
    "binance": float(os.getenv("FEE_BINANCE", "0.001")),   # 0.1%
    "kucoin": float(os.getenv("FEE_KUCOIN", "0.001")),     # 0.1%
    "mexc": float(os.getenv("FEE_MEXC", "0.002")),         # 0.2%
    "bitget": float(os.getenv("FEE_BITGET", "0.002")),     # 0.2%
    "okx": float(os.getenv("FEE_OKX", "0.002"))            # 0.2%
}

# Withdrawal fees that matter if you plan to transfer asset between exchanges (optional)
WITHDRAW_FEE_ASSET = {
    # e.g. "binance": {"ETH": 0.005}, but not used in conservative immediate-sell calculations
}

# Symbol discovery mode: "TOP_N" or "WATCHLIST" or "ALL_COMMON"
MODE = os.getenv("MODE", "TOP_N")
TOP_N = int(os.getenv("TOP_N", "80"))  # used if MODE == TOP_N
WATCHLIST = os.getenv("WATCHLIST", "")  # comma-separated "ETHUSDT,BTCUSDT"

CSV_LOG = os.getenv("CSV_LOG", "arbitrage_alerts.csv")  # log alerts

# ==================================

# ========== HELPERS: symbol mapping & endpoints ==========
# We will normalize symbol to "ETHUSDT" format for Binance-style pairs.
def normalize_symbol_for(exchange, symbol):
    # Accept input in forms "ETHUSDT" or "ETH-USDT" or "ETH/USDT"
    s = symbol.replace("/", "").replace("-", "").upper()
    if exchange == "kucoin":
        # kucoin expects "ETH-USDT" usually, but API accepts "ETH-USDT" for some endpoints
        return symbol.replace("/", "-") if "-" in symbol or "/" in symbol else symbol[:3] + "-" + symbol[3:]
    if exchange == "okx":
        # OKX uses "ETH-USDT" for instId
        return symbol[:3] + "-" + symbol[3:]
    if exchange == "mexc":
        return symbol.replace("/", "_") if "/" in symbol else symbol[:3] + "_" + symbol[3:]
    # default (binance/bitget) use "ETHUSDT"
    return s

# Adapter functions per exchange to fetch top N depth and return list of bids/asks as [(price, qty),...]
def fetch_binance_depth(symbol, limit=DEPTH_LEVELS):
    url = "https://api.binance.com/api/v3/depth"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=8)
        r.raise_for_status()
        data = r.json()
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])]
        return bids[:limit], asks[:limit]
    except Exception as e:
        # print("Binance fetch error:", e)
        return None, None

def fetch_kucoin_depth(symbol, limit=DEPTH_LEVELS):
    # symbol like ETH-USDT
    url = "https://api.kucoin.com/api/v1/market/orderbook/level2"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data", {})
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])][:limit]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])][:limit]
        return bids, asks
    except Exception:
        return None, None

def fetch_mexc_depth(symbol, limit=DEPTH_LEVELS):
    # MEXC symbol "ETH_USDT"
    url = "https://www.mexc.com/api/v2/market/depth"
    try:
        r = requests.get(url, params={"symbol": symbol, "depth": limit}, timeout=8)
        r.raise_for_status()
        d = r.json().get("data", {})
        bids = [(float(p), float(q)) for p, q in d.get("bids", [])][:limit]
        asks = [(float(p), float(q)) for p, q in d.get("asks", [])][:limit]
        return bids, asks
    except Exception:
        return None, None

def fetch_bitget_depth(symbol, limit=DEPTH_LEVELS):
    url = "https://api.bitget.com/api/spot/v1/market/depth"
    try:
        r = requests.get(url, params={"symbol": symbol, "limit": limit}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data", {})
        bids = [(float(p), float(q)) for p, q in data.get("bids", [])][:limit]
        asks = [(float(p), float(q)) for p, q in data.get("asks", [])][:limit]
        return bids, asks
    except Exception:
        return None, None

def fetch_okx_depth(symbol, limit=DEPTH_LEVELS):
    # OKX uses instId like ETH-USDT
    url = "https://www.okx.com/api/v5/market/books"
    try:
        r = requests.get(url, params={"instId": symbol, "sz": limit}, timeout=8)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None, None
        d = data[0]
        bids = [(float(p), float(q)) for p, q, *_ in d.get("bids", [])][:limit]
        asks = [(float(p), float(q)) for p, q, *_ in d.get("asks", [])][:limit]
        return bids, asks
    except Exception:
        return None, None

# generic fetch dispatcher
FETCHERS = {
    "binance": fetch_binance_depth,
    "kucoin": fetch_kucoin_depth,
    "mexc": fetch_mexc_depth,
    "bitget": fetch_bitget_depth,
    "okx": fetch_okx_depth
}

# utility: get best aggregated price to buy 'notional' USDT on exchange (i.e., walk asks to buy base)
def estimate_buy_cost_from_depth(asks, target_notional_usdt):
    """
    asks: [(price, qty), ...] (ascending prices)
    target_notional_usdt: how much USDT we want to spend to buy base
    returns: (executed_notional, acquired_base_qty, avg_price, levels_used_list)
    """
    remain = target_notional_usdt
    acquired = 0.0
    spent = 0.0
    levels = []
    for price, qty in asks:
        level_notional = price * qty
        if remain <= 0:
            break
        use = min(level_notional, remain)
        qty_used = use / price
        acquired += qty_used
        spent += use
        remain -= use
        levels.append((price, qty_used, use))
    executed = spent
    avg_price = (spent / acquired) if acquired > 0 else None
    return executed, acquired, avg_price, levels

# utility: estimate receive when selling base_qty on bids side
def estimate_sell_receive_from_depth(bids, target_base_qty):
    """
    bids: [(price, qty), ...] descending prices
    target_base_qty: how many base units to sell
    returns: (received_notional, sold_base_qty, avg_price, levels_used_list)
    """
    remain = target_base_qty
    received = 0.0
    sold = 0.0
    levels = []
    for price, qty in bids:
        if remain <= 0:
            break
        use_qty = min(qty, remain)
        notional = use_qty * price
        received += notional
        sold += use_qty
        remain -= use_qty
        levels.append((price, use_qty, notional))
    avg_price = (received / sold) if sold > 0 else None
    return received, sold, avg_price, levels

# compute max profitable notional by walking both sides
def find_max_profitable_notional(buy_asks, sell_bids, fee_buy, fee_sell, capital_limit):
    """
    buy_asks: asks on buy exchange (price ascending)
    sell_bids: bids on sell exchange (price descending)
    We will attempt incremental notional steps across book levels to find max notional
    that yields positive profit after fees. We cap by capital_limit (USDT).
    Return estimated notional to use, profit_usdt, profit_pct, detail.
    """
    # We'll test incremental notional slices based on level notional amounts
    # Build cumulative notional steps from both sides
    buy_level_notional = [p*q for p,q in buy_asks]
    sell_level_notional = [p*q for p,q in sell_bids]
    # Simplest approach: evaluate candidate notional values using steps of min level notional
    step_candidates = []
    for n in buy_level_notional[:MAX_WALK_LEVELS]:
        step_candidates.append(n)
    for n in sell_level_notional[:MAX_WALK_LEVELS]:
        step_candidates.append(n)
    # add small fractions of capital
    step_candidates += [capital_limit * frac for frac in (0.05, 0.1, 0.2, 0.5, 1.0)]
    # deduplicate & sort ascending
    cand = sorted(set([max(1.0, float(round(x, 2))) for x in step_candidates if x > 0]))
    best = {"notional": 0.0, "profit": -1e9, "profit_pct": -999.0, "buy_exec": None, "sell_exec": None}
    for target in cand:
        if target > capital_limit + 1e-6:
            continue
        # buy side: execute target USDT across asks
        buy_executed, base_acquired, buy_avg, buy_levels = estimate_buy_cost_from_depth(buy_asks, target)
        if base_acquired <= 0:
            continue
        # sell side: need to sell base_acquired across bids on sell side
        sell_received, base_sold, sell_avg, sell_levels = estimate_sell_receive_from_depth(sell_bids, base_acquired)
        if base_sold <= 0:
            continue
        # apply taker fees
        cost_with_fee = buy_executed * (1.0 + fee_buy)
        receive_after_fee = sell_received * (1.0 - fee_sell)
        profit = receive_after_fee - cost_with_fee
        profit_pct = (profit / cost_with_fee * 100.0) if cost_with_fee > 0 else 0.0
        if profit > best["profit"] and profit > 0:
            best = {
                "notional": buy_executed,
                "profit": profit,
                "profit_pct": profit_pct,
                "buy_exec": (buy_executed, base_acquired, buy_avg, buy_levels),
                "sell_exec": (sell_received, base_sold, sell_avg, sell_levels)
            }
    return best

# Telegram helper
def send_telegram(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_TOKEN or CHAT_ID env vars")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print("Telegram send error:", e)

# CSV logging
def log_alert(row):
    header = ["timestamp","symbol","buy_ex","sell_ex","notional","profit_usdt","profit_pct","buy_levels","sell_levels"]
    exists = False
    try:
        exists = os.path.exists(CSV_LOG)
    except:
        exists = False
    with open(CSV_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(header)
        writer.writerow(row)

# Normalize symbol lists across exchanges (simple approach)
def canonical_symbol_list():
    # If WATCHLIST provided use it
    if WATCHLIST:
        return [s.strip().upper().replace("-","").replace("/","") for s in WATCHLIST.split(",") if s.strip()]
    # Otherwise we will use a default set of common USDT pairs (top coins)
    top_pairs = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","LTCUSDT","ADAUSDT","DOGEUSDT","MATICUSDT","AVAXUSDT"]
    # limit by TOP_N if requested (we choose top_pairs up to TOP_N)
    return top_pairs[:TOP_N]

# Main loop: scan cross-exchange combos
def run_loop():
    symbols = canonical_symbol_list()
    print(f"Starting arbitrage monitor for {len(symbols)} symbols across {EXCHANGES}. Capital: {CAPITAL_USDT} USDT")
    while True:
        try:
            for symbol in symbols:
                # build depth per exchange
                depths = {}
                for ex in EXCHANGES:
                    ex = ex.strip().lower()
                    norm = normalize_symbol_for(ex, symbol)
                    fetcher = FETCHERS.get(ex)
                    if not fetcher:
                        continue
                    bids, asks = fetcher(norm, DEPTH_LEVELS)
                    if not bids or not asks:
                        continue
                    # transform asks to ascending, bids to descending (some endpoints already that way)
                    # for safer handling, sort:
                    asks_sorted = sorted(asks, key=lambda x: x[0])[:DEPTH_LEVELS]
                    bids_sorted = sorted(bids, key=lambda x: -x[0])[:DEPTH_LEVELS]
                    depths[ex] = {"bids": bids_sorted, "asks": asks_sorted}
                # now for every pair of exchanges check buy on A (asks) and sell on B (bids)
                exch_list = list(depths.keys())
                for i in range(len(exch_list)):
                    for j in range(len(exch_list)):
                        if i == j:
                            continue
                        buy_ex = exch_list[i]
                        sell_ex = exch_list[j]
                        buy_book = depths[buy_ex]["asks"]   # to buy we walk asks
                        sell_book = depths[sell_ex]["bids"] # to sell we walk bids
                        fee_buy = TAKER_FEES.get(buy_ex, 0.001)
                        fee_sell = TAKER_FEES.get(sell_ex, 0.001)
                        # find best profitable notional up to CAPITAL_USDT
                        best = find_max_profitable_notional(buy_book, sell_book, fee_buy, fee_sell, CAPITAL_USDT)
                        if best["profit"] > 0 and best["notional"] >= 1.0:
                            # check thresholds
                            if best["profit_pct"] >= MIN_PROFIT_PCT and best["profit"] >= MIN_PROFIT_USDT:
                                # Prepare message
                                ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                                buy_exec = best["buy_exec"]
                                sell_exec = best["sell_exec"]
                                buy_avg = buy_exec[2] if buy_exec else None
                                sell_avg = sell_exec[2] if sell_exec else None
                                msg_lines = [
                                    f"🚨 ARB Opportunity {symbol}  [{ts}]",
                                    f"Buy on {buy_ex.upper()} - avg {buy_avg:.6f} (USDT)  | Sell on {sell_ex.upper()} - avg {sell_avg:.6f}",
                                    f"Est Notional (USDT): {best['notional']:.2f}",
                                    f"Est Profit (USDT): {best['profit']:.2f}  ({best['profit_pct']:.2f}%)",
                                    f"Taker Fees: buy {fee_buy*100:.2f}%  sell {fee_sell*100:.2f}%",
                                    f"Capital cap used: {CAPITAL_USDT:.2f} USDT",
                                    f"Buy levels used (price, base_qty, notional): {[(p, round(q,6), round(n,2)) for p,q,n in buy_exec[3]]}",
                                    f"Sell levels used (price, base_qty, notional): {[(p, round(q,6), round(n,2)) for p,q,n in sell_exec[3]]}",
                                    "⚠️ Note: This is ALERT-ONLY. Manual execution needed. Consider depth, withdrawal fees & transfer time."
                                ]
                                text = "\n".join(msg_lines)
                                send_telegram(text)
                                # log CSV
                                log_alert([ts, symbol, buy_ex, sell_ex, round(best["notional"],2), round(best["profit"],2), round(best["profit_pct"],3), str(buy_exec[3]), str(sell_exec[3])])
                                # small pause to avoid spamming
                                time.sleep(1)
            # finished symbols iteration
        except Exception as e:
            print("Main loop exception:", e)
        # wait before next cycle
        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    run_loop()
