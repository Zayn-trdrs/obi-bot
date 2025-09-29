import requests

BOT_TOKEN = "your-correct-bot-token"
CHAT_ID = "your-chat-id"

resp = requests.get(
    f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
)
print(resp.json())
