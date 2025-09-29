import os
import time
import requests
import asyncio
import aiohttp
import telegram

# ==== CONFIG ====
TELEGRAM_TOKEN = "7966125271:AAGBFT3RAom7EyRcFeKQGONbrNUYgA-DA8g"
CHAT_ID = "8456437457"
HEARTBEAT_INTERVAL = 300  # 5 minutes
SCAN_INTERVAL = 60  # scan every 60 sec

# Exchanges and API endpoints
EXCHANGES = {
    "binance": "https://api.binance.com/api/v3/ticker/bookTicker",
    "kucoin": "https://api.kucoin.com/api/v1/market/allTickers",
    "mexc": "https://api.mexc.com/api/v3/ticker/bookTicker",
    "bitget": "https://api.bitget.com/api/spot/v1/market/tickers",
    "okx": "https://www.okx.com/api/v5/market/tickers?instType=SPOT"
}

bot = telegram.Bot(token=TELEGRAM_TOKEN)


async def fetch(session, url, name):
    try:
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            return name, data
    except Exception as e:
        return name, {"error": str(e)}


def parse_data(name, data):
    """Extract {symbol: (bid, ask)} from each exchange"""
    result = {}
    try:
        if name == "binance":
            for item in data:
                symbol = item["symbol"]
                if symbol.endswith("USDT"):
                    result[symbol] = (float(item["bidPrice"]), float(item["askPrice"]))
        elif name == "kucoin":
            for item in data["data"]["ticker"]:
                symbol = item["symbol"].replace("-", "")
                if symbol.endswith("USDT"):
                    result[symbol] = (float(item["buy"]), float(item["sell"]))
        elif name == "mexc":
            for item in data:
                symbol = item["symbol"]
                if symbol.endswith("USDT"):
                    result[symbol] = (float(item["bidPrice"]), float(item["askPrice"]))
        elif name == "bitget":
            for item in data["data"]:
                symbol = item["symbol"].replace("_", "")
                if symbol.endswith("USDT"):
                    result[symbol] = (float(item["buyOne"]), float(item["sellOne"]))
        elif name == "okx":
            for item in data["data"]:
                instId = item["instId"].replace("-", "")
                if instId.endswith("USDT"):
                    result[instId] = (float(item["bidPx"]), float(item["askPx"]))
    except Exception:
        pass
    return result


async def scan_arbitrage():
    async with aiohttp.ClientSession() as session:
        tasks = [fetch(session, url, name) for name, url in EXCHANGES.items()]
        results = await asyncio.gather(*tasks)

    markets = {}
    for name, data in results:
        if "error" not in data:
            markets[name] = parse_data(name, data)

    spreads = []
    symbols = set()
    for m in markets.values():
        symbols.update(m.keys())

    for sym in list(symbols)[:30]:  # limit top 30 symbols to avoid spam
        best_bid, best_bid_ex = -1, None
        best_ask, best_ask_ex = 1e20, None
        for ex, book in markets.items():
            if sym in book:
                bid, ask = book[sym]
                if bid > best_bid:
                    best_bid, best_bid_ex = bid, ex
                if ask < best_ask:
                    best_ask, best_ask_ex = ask, ex
        if best_bid > 0 and best_ask < 1e20 and best_bid_ex != best_ask_ex:
            spread = (best_bid - best_ask) / best_ask * 100
            spreads.append((sym, best_ask, best_ask_ex, best_bid, best_bid_ex, spread))

    return spreads


async def main():
    last_heartbeat = 0
    while True:
        try:
            spreads = await scan_arbitrage()
            if spreads:
                msg = "🔍 Arbitrage Scan Results:\n"
                for sym, ask, ask_ex, bid, bid_ex, spread in spreads:
                    msg += f"{sym}: Buy {ask_ex} @ {ask:.4f}, Sell {bid_ex} @ {bid:.4f} → Spread {spread:.2f}%\n"
                await bot.send_message(chat_id=CHAT_ID, text=msg[:4000])
            else:
                await bot.send_message(chat_id=CHAT_ID, text="No arbitrage opportunities found.")

            # Heartbeat
            if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                await bot.send_message(chat_id=CHAT_ID, text="✅ Bot is scanning exchanges...")
                last_heartbeat = time.time()

        except Exception as e:
            await bot.send_message(chat_id=CHAT_ID, text=f"❌ Error: {e}")

        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
