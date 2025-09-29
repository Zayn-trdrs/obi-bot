import requests

BOT_TOKEN = "your-bot-token-here"
CHAT_ID = "your-chat-id-here"

resp = requests.get(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": "Hello from Render test ✅"}
)

print(resp.json())
