import os
import telebot
import re
import time
import threading

from config import BOT_TOKEN, CHECK_INTERVAL, ALERT_THRESHOLD
from dex import get_usdt_pair
from analyzer import anti_rug_score, is_pump
from storage import coins, last_alert
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

BOT_TOKEN = BOT_TOKEN.strip()  # 🔥 ВАЖНО
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

DEX_REGEX = r"0x[a-fA-F0-9]{40}"

@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(
        msg.chat.id,
        "🚀 Мемкоин бот\n\n"
        "Пришли ссылку Dexscreener — я начну следить 👇"
    )

@bot.message_handler(func=lambda m: True)
def add_coin(msg):
    match = re.search(DEX_REGEX, msg.text)
    if not match:
        return

    address = match.group(0)
    data = get_usdt_pair(address)

    if not data:
        bot.send_message(msg.chat.id, "❌ USDT-пара не найдена")
        return

    coins[address] = msg.chat.id
    last_alert[address] = data["price"]

    bot.send_message(
        msg.chat.id,
        f"✅ Монета добавлена\n"
        f"*{data['symbol']}* ({data['name']})\n"
        f"🔗 {data['dex']}"
    )

def watcher():
    while True:
        for addr, chat in list(coins.items()):
            data = get_usdt_pair(addr)
            if not data:
                continue

            change = data["change"]

            if abs(change) >= ALERT_THRESHOLD:
                risk = anti_rug_score(
                    data["liquidity"],
                    data["volume"],
                    change
                )

                pump = "🚀 PUMP" if is_pump(change, data["volume"]) else ""

                bot.send_message(
                    chat,
                    f"{pump}\n"
                    f"*{data['symbol']}* ({data['name']})\n"
                    f"💰 ${data['price']}\n"
                    f"📈 1h: {change}%\n"
                    f"⚠️ Риск: {risk}%"
                )

                last_alert[addr] = data["price"]

        time.sleep(CHECK_INTERVAL)

threading.Thread(target=watcher, daemon=True).start()

bot.infinity_polling(skip_pending=True)
