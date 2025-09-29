import asyncio
import aiohttp
import numpy as np
import pandas as pd
import talib

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

obi_history = []
cvd_history = []

# ===============================
# FUNCTIONS
# ===============================
async def send_telegram_message(message):
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        async with session.post(url, data=payload) as resp:
            return await resp.text()

async def get_order_book(session, symbol, limit=OBI_LEVELS):
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()
        bids = np.array([[float(price), float(qty)] for price, qty in data['bids']])
        asks = np.array([[float(price), float(qty)] for price, qty in data['asks']])
        return bids, asks

async def get_klines(session, symbol, interval="1m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with session.get(url) as resp:
        data = await resp.json()
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

# ===============================
# MAIN LOOP
# ===============================
async def main():
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                bids, asks = await get_order_book(session, SYMBOL)
                df = await get_klines(session, SYMBOL)

                # Weighted OBI
                WOBI = calculate_weighted_obi(bids, asks)
                obi_history.append(WOBI)
                z_obi = calculate_z_score(obi_history[-20:])

                # TMS
                tms = calculate_tms(df)

                # CVD exhaustion
                cvd = df['taker_buy_base'].iloc[-1] - df['taker_buy_base'].iloc[-2]
                cvd_history.append(cvd)
                z_cvd = calculate_z_score(cvd_history[-20:])

                # ATR-based position size
                atr = calculate_atr(df)
                account_balance = 1000
                position_size = (account_balance * POSITION_RISK) / (atr+1e-6)

                # Signal conditions
                if z_obi > Z_THRESHOLD and tms > TMS_THRESHOLD and z_cvd < EXHAUSTION_Z:
                    await send_telegram_message(f"🚀 LONG | Size: {position_size:.4f} | WOBI z:{z_obi:.2f} TMS:{tms:.4f}")

                elif z_obi < -Z_THRESHOLD and tms < -TMS_THRESHOLD and z_cvd > -EXHAUSTION_Z:
                    await send_telegram_message(f"🔻 SHORT | Size: {position_size:.4f} | WOBI z:{z_obi:.2f} TMS:{tms:.4f}")

                await asyncio.sleep(1)  # prevent CPU overload

            except Exception as e:
                print("Error:", e)
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
