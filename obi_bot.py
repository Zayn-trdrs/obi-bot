import os
from flask import Flask, request
import telebot
from telebot import types
import requests
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)  # Flask instance named 'app' for gunicorn

# Environment variables
TOKEN = os.environ['TELEGRAM_TOKEN']
WEBHOOK_URL = os.environ['WEBHOOK_URL']  # e.g., https://obi-bot.onrender.com (no /webhook)
bot = telebot.TeleBot(TOKEN)

# Set webhook on startup (runs even under Gunicorn)
def setup_webhook():
    try:
        bot.remove_webhook()
        full_webhook_url = f"{WEBHOOK_URL}/webhook"
        response = bot.set_webhook(url=full_webhook_url)
        if response:
            logger.info(f"Webhook set successfully to {full_webhook_url}")
        else:
            logger.error("Failed to set webhook - empty response")
    except Exception as e:
        logger.error(f"Webhook URL error: {e}")

setup_webhook()  # Call immediately

@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id, "Welcome to AI Trading Bot! Use /predict for a BTC price prediction and signal based on linear regression model.")

@bot.message_handler(commands=['predict'])
def predict(message):
    try:
        # Fetch last 10 BTC hourly prices from CoinGecko (free API, no key needed)
        url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=1&interval=hourly"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        prices = [p[1] for p in data['prices'][-10:]]  # Last 10 prices
        
        if len(prices) < 2:
            bot.send_message(message.chat.id, "Error: Insufficient data for prediction.")
            return
        
        # Prepare data for "AI" prediction (simple linear regression on time series)
        X = np.array(range(len(prices))).reshape(-1, 1)
        y = np.array(prices)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        model = LinearRegression()
        model.fit(X_scaled, y)
        
        # Predict next price
        next_x = scaler.transform([[len(prices)]])
        pred = model.predict(next_x)[0]
        
        # Generate signal
        signal = "BUY" if pred > prices[-1] else "SELL"
        change_pct = ((pred - prices[-1]) / prices[-1]) * 100
        
        msg = (
            f"🧠 AI Prediction (Linear Regression on last 10h BTC data):\n"
            f"Current BTC: ${prices[-1]:.2f}\n"
            f"Predicted Next: ${pred:.2f} ({change_pct:+.2f}%)\n"
            f"Signal: {signal}\n\n"
            f"⚠️ This is a demo—DYOR & manage risks!"
        )
        bot.send_message(message.chat.id, msg)
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        bot.send_message(message.chat.id, f"Error generating prediction: {str(e)}")

@app.route('/', methods=['GET'])
def index():
    return "AI Trading Bot is running! Check logs for details."

@app.route('/setwebhook', methods=['GET'])
def manual_set_webhook():
    setup_webhook()
    return "Webhook setup attempted—check logs!"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'ok'
    else:
        return 'Unauthorized', 403

if __name__ == '__main__':
    # Fallback for local runs
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
