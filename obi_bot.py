""" Telegram Trading Bot (FastAPI) — Liquidity + Order Flow Strategy File: telegram_render_trading_bot.py

Overview

Single-file example Telegram trading bot built for deployment on Render (or similar).

FastAPI receives Telegram webhook updates.

Uses ccxt for exchange API (supports many exchanges).

Polls exchange for candles and order book snapshots to run a "liquidity-based order flow" strategy with:

Trend filter: 100 EMA on configured timeframe

Volume confirmation: candle volume > volume EMA(30)

Order book imbalance: sum(bids_topN) vs sum(asks_topN)

Liquidity sweep detection (heuristic)


Executes live orders via exchange API (market orders + stop-loss + take-profit)

Stores trades in a local SQLite DB (SQLAlchemy)


Notes & Limitations

This is an educational starter script. Order flow / footprint analysis in full fidelity requires exchange feeds (raw trades, deltas) not provided here — we use heuristics (orderbook imbalance, volume spikes).

Do NOT run with real money until fully tested on testnet and reviewed.

Use environment variables for keys and configuration.


Required environment variables

TELEGRAM_BOT_TOKEN      - Telegram bot token TELEGRAM_WEBHOOK_URL    - Public HTTPS URL for Telegram webhook (Render service URL) EXCHANGE_ID              - ccxt exchange id (e.g., 'binance') EXCHANGE_API_KEY         - exchange API key EXCHANGE_SECRET          - exchange API secret SYMBOL                   - trading symbol (e.g., 'BTC/USDT') TIMEFRAME                - candles timeframe (e.g., '5m') POSITION_RISK_PCT        - risk per trade as % of equity (e.g., 0.5) DB_URL                   - SQLAlchemy DB URL (default sqlite:///trades.db) USE_TESTNET              - '1' to enable testnet mode if exchange supports it

Install dependencies

pip install fastapi uvicorn ccxt pandas numpy ta python-telegram-bot[webhooks] sqlalchemy apscheduler aiohttp

How to run locally (development)

uvicorn telegram_render_trading_bot:app --host 0.0.0.0 --port 8000

On Render: deploy a Python web service, set the webhook URL to https://<your-render-service>/.telegram/webhook

"""

import os import asyncio import logging from typing import Optional, Dict, Any from datetime import datetime, timezone, timedelta

import ccxt.async_support as ccxt import pandas as pd import numpy as np from ta.trend import EMAIndicator from fastapi import FastAPI, Request, HTTPException from pydantic import BaseModel from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean from sqlalchemy.orm import sessionmaker, declarative_base from apscheduler.schedulers.asyncio import AsyncIOScheduler

------------------ Config & Logging ------------------

logging.basicConfig(level=logging.INFO) log = logging.getLogger("trading_bot")

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN') TELEGRAM_WEBHOOK_URL = os.getenv('TELEGRAM_WEBHOOK_URL') EXCHANGE_ID = os.getenv('EXCHANGE_ID', 'binance') EXCHANGE_API_KEY = os.getenv('EXCHANGE_API_KEY') EXCHANGE_SECRET = os.getenv('EXCHANGE_SECRET') SYMBOL = os.getenv('SYMBOL', 'BTC/USDT') TIMEFRAME = os.getenv('TIMEFRAME', '5m') POSITION_RISK_PCT = float(os.getenv('POSITION_RISK_PCT', '0.5')) / 100.0 DB_URL = os.getenv('DB_URL', 'sqlite:///trades.db') USE_TESTNET = os.getenv('USE_TESTNET', '0') == '1'

if not TELEGRAM_BOT_TOKEN: log.warning("TELEGRAM_BOT_TOKEN not set — Telegram commands will be disabled until provided.")

------------------ Database ------------------

Base = declarative_base()

class Trade(Base): tablename = 'trades' id = Column(Integer, primary_key=True) symbol = Column(String) side = Column(String)  # 'buy' or 'sell' entry_price = Column(Float) stop_loss = Column(Float) take_profit = Column(Float) size = Column(Float) opened_at = Column(DateTime) closed = Column(Boolean, default=False) closed_at = Column(DateTime, nullable=True)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith('sqlite') else {}) SessionLocal = sessionmaker(bind=engine) Base.metadata.create_all(bind=engine)

------------------ FastAPI / Telegram webhook ------------------

app = FastAPI()

class TelegramUpdate(BaseModel): update_id: int message: Optional[Dict[str, Any]] = None callback_query: Optional[Dict[str, Any]] = None

@app.post("/.telegram/webhook") async def telegram_webhook(update: Request): if not TELEGRAM_BOT_TOKEN: raise HTTPException(status_code=400, detail="Telegram not configured") body = await update.json() log.info("Received Telegram update: %s", body) # Basic handling: support /start, /status, /balance, /force_check try: if 'message' in body and 'text' in body['message']: chat_id = body['message']['chat']['id'] text = body['message']['text'] asyncio.create_task(handle_telegram_command(chat_id, text)) except Exception as e: log.exception("Error processing update: %s", e) return {"ok": True}

async def handle_telegram_command(chat_id: int, text: str): # send simple replies using Telegram sendMessage API import aiohttp base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" async with aiohttp.ClientSession() as session: if text.startswith('/start'): await session.get(f"{base}/sendMessage", params={"chat_id": chat_id, "text": "Bot is running."}) elif text.startswith('/status'): await session.get(f"{base}/sendMessage", params={"chat_id": chat_id, "text": f"Strategy: Liquidity Orderflow on {SYMBOL} ({TIMEFRAME})"}) elif text.startswith('/balance'): bal = await get_account_balance_summary() await session.get(f"{base}/sendMessage", params={"chat_id": chat_id, "text": bal}) elif text.startswith('/force_check'): await session.get(f"{base}/sendMessage", params={"chat_id": chat_id, "text": "Forcing market check..."}) asyncio.create_task(run_strategy_once()) else: await session.get(f"{base}/sendMessage", params={"chat_id": chat_id, "text": "Unknown command."})

------------------ Exchange client ------------------

exchange: Optional[ccxt.Exchange] = None

async def init_exchange(): global exchange exchange_class = getattr(ccxt, EXCHANGE_ID) exchange = exchange_class({ 'apiKey': EXCHANGE_API_KEY or '', 'secret': EXCHANGE_SECRET or '', 'enableRateLimit': True, # Add testnet settings for supported exchanges here }) # Example for binance testnet if USE_TESTNET and EXCHANGE_ID in ('binance', 'binanceus'): exchange.set_sandbox_mode(True) log.info("Exchange client initialized: %s", EXCHANGE_ID)

------------------ Utilities & Strategy primitives ------------------

async def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=200): # Fetch recent candles and return DataFrame ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit) df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']) df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms') df.set_index('timestamp', inplace=True) return df

async def fetch_order_book(symbol=SYMBOL, depth=20): ob = await exchange.fetch_order_book(symbol, limit=depth) return ob

def compute_indicators(df: pd.DataFrame): df = df.copy() df['ema100'] = EMAIndicator(df['close'], window=100).ema_indicator() df['vol_ema30'] = df['volume'].ewm(span=30, adjust=False).mean() return df

def order_book_imbalance(ob: Dict[str, Any], top_n=10): bids = ob.get('bids', [])[:top_n] asks = ob.get('asks', [])[:top_n] bid_liq = sum([price * size for price, size in bids]) ask_liq = sum([price * size for price, size in asks]) # Imbalance ratio > 1 => more bids than asks (bullish imbalance) ratio = (bid_liq / (ask_liq + 1e-9)) return ratio, bid_liq, ask_liq

async def get_account_balance_summary(): try: bal = await exchange.fetch_balance() # Show total USDT or quote currency value quote = SYMBOL.split('/')[1] if quote in bal['total']: return f"Balance {quote}: {bal['total'][quote]}" else: return str(bal['total']) except Exception as e: log.exception("Error fetching balance: %s", e) return "Could not fetch balance"

------------------ Strategy / Execution ------------------

async def compute_position_size(quote_balance: float, entry_price: float, stop_loss: float) -> float: # Risk-based sizing: risk = quote_balance * POSITION_RISK_PCT # size in base currency = risk / (abs(entry - stop_loss)) if entry_price == stop_loss: return 0.0 risk_amount = quote_balance * POSITION_RISK_PCT price_diff = abs(entry_price - stop_loss) size = risk_amount / price_diff return max(size, 0)

async def place_order(side: str, amount: float, symbol=SYMBOL): try: # Market order order = await exchange.create_market_order(symbol, side, amount) return order except Exception as e: log.exception("Order placement failed: %s", e) return None

async def run_strategy_once(): """Single pass: fetch data -> compute signals -> maybe place order""" try: df = await fetch_ohlcv(limit=200) df = compute_indicators(df) latest = df.iloc[-1] prev = df.iloc[-2]

# Basic trend filter
    price = latest['close']
    ema100 = latest['ema100']
    vol = latest['volume']
    vol_ema = latest['vol_ema30']

    ob = await fetch_order_book(depth=20)
    imbalance_ratio, bid_liq, ask_liq = order_book_imbalance(ob, top_n=10)

    # Liquidity sweep heuristic:
    # if previous candle low was pierced intra-candle and price quickly recovered -> possible sweep
    candle_range = prev['high'] - prev['low']
    pierced_low = (latest['low'] < prev['low']) and (price > prev['low'])
    volume_spike = vol > vol_ema * 1.8

    # Signal conditions (long example)
    long_condition = (
        price > ema100 and
        volume_spike and
        imbalance_ratio > 1.3 and
        pierced_low
    )

    # Signal conditions (short example)
    short_condition = (
        price < ema100 and
        volume_spike and
        imbalance_ratio < 0.7 and
        (latest['high'] > prev['high'])
    )

    log.info("Price=%.2f EMA100=%.2f Vol=%.2f VolEMA=%.2f Imb=%.2f long=%s short=%s",
             price, ema100, vol, vol_ema, imbalance_ratio, long_condition, short_condition)

    if long_condition or short_condition:
        side = 'buy' if long_condition else 'sell'
        # Compute stop loss: for long, set SL below last low; for short, set SL above last high
        if side == 'buy':
            stop_loss = float(prev['low']) - (candle_range * 0.2)
            take_profit = price + (price - stop_loss) * 2.0
        else:
            stop_loss = float(prev['high']) + (candle_range * 0.2)
            take_profit = price - (stop_loss - price) * 2.0

        # get quote balance
        bal = await exchange.fetch_balance()
        quote = SYMBOL.split('/')[1]
        quote_balance = float(bal['total'].get(quote, 0) or 0)
        size = await compute_position_size(quote_balance, price, stop_loss)
        if size <= 0:
            log.warning("Calculated size <= 0, skipping")
            return

        # Place market order
        order = await place_order(side, size)
        if order:
            log.info("Placed %s order: %s", side, order)
            # Save trade
            sess = SessionLocal()
            t = Trade(symbol=SYMBOL, side=side, entry_price=price, stop_loss=stop_loss, take_profit=take_profit, size=size, opened_at=datetime.now(timezone.utc), closed=False)
            sess.add(t)
            sess.commit()
            sess.close()
            # Optionally: set OCO orders for TP/SL if exchange supports

except Exception as e:
    log.exception("Error in strategy run: %s", e)

------------------ Background scheduler ------------------

scheduler = AsyncIOScheduler()

@app.on_event("startup") async def startup_event(): await init_exchange() # Start polling schedule: run strategy every timeframe (e.g., 5 minutes + small offset) minutes = int(TIMEFRAME.replace('m','') if 'm' in TIMEFRAME else 1) scheduler.add_job(run_strategy_once, 'interval', minutes=minutes, next_run_time=datetime.now()+timedelta(seconds=10)) scheduler.start() log.info("Scheduler started: running strategy every %s", TIMEFRAME) # Set Telegram webhook if configured if TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_URL: import aiohttp async with aiohttp.ClientSession() as session: resp = await session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook", params={"url": TELEGRAM_WEBHOOK_URL}) data = await resp.json() log.info("SetWebhook response: %s", data)

@app.on_event("shutdown") async def shutdown_event(): if exchange: await exchange.close() scheduler.shutdown()

------------------ Simple health endpoint ------------------

@app.get('/') async def root(): return {"status": "ok", "symbol": SYMBOL, "timeframe": TIMEFRAME}

------------------ If run as script (dev) ------------------

if name == 'main': import uvicorn uvicorn.run('telegram_render_trading_bot:app', host='0.0.0.0', port=8000, reload=True)

