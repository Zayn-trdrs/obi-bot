import time, requests

BOT_TOKEN = "your-bot-token"
CHAT_ID = "your-chat-id"

def send_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": text})
        print(r.json())
    except Exception as e:
        print("Error sending:", e)

while True:
    send_message("Worker is alive ✅")
    time.sleep(60)   # send every minute
