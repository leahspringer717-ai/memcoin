import os
import json
import requests
import telebot
from telebot import types

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

DATA_FILE = "coins.json"

# Хранилище callback данных (чтобы не превышать 64 байта)
callback_storage = {}

# ------------------ UTILS ------------------

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_price(pair_address):
    url = f"https://api.dexscreener.com/latest/dex/pairs/{pair_address}"
    r = requests.get(url)
    data = r.json()
    pair = data["pair"]
    return float(pair["priceUsd"]), pair["baseToken"]["name"]

# ------------------ START ------------------

@bot.message_handler(commands=["start"])
def start(message):
    text = """
🚀 <b>Dex Screener Bot</b>

Функционал:
• Добавление монет
• Отслеживание цены
• Расчёт спреда
• Уведомления по %
• Мои монеты

Выбери действие ниже 👇
"""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Добавить монету", callback_data="add_coin"))
    markup.add(types.InlineKeyboardButton("📊 Мои монеты", callback_data="my_coins"))
    markup.add(types.InlineKeyboardButton("ℹ️ О боте", callback_data="about"))

    bot.send_message(message.chat.id, text, reply_markup=markup)

# ------------------ CALLBACKS ------------------

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    if call.data == "add_coin":
        msg = bot.send_message(call.message.chat.id, "Вставь pair address с DexScreener:")
        bot.register_next_step_handler(msg, process_add_coin)

    elif call.data == "my_coins":
        show_my_coins(call.message)

    elif call.data == "about":
        bot.send_message(call.message.chat.id, "Бот отслеживает любые пары с DexScreener.\nПоддержка всех сетей.")

    elif call.data.startswith("coin_"):
        coin_id = call.data.split("_")[1]
        coin_data = callback_storage.get(coin_id)

        if not coin_data:
            bot.answer_callback_query(call.id, "Монета не найдена")
            return

        price, name = get_price(coin_data["pair"])
        bot.send_message(
            call.message.chat.id,
            f"📈 <b>{name}</b>\nЦена: ${price}"
        )

# ------------------ ADD COIN ------------------

def process_add_coin(message):
    pair_address = message.text.strip()

    try:
        price, name = get_price(pair_address)
    except:
        bot.send_message(message.chat.id, "❌ Неверный pair address")
        return

    data = load_data()
    user_id = str(message.chat.id)

    if user_id not in data:
        data[user_id] = []

    data[user_id].append({
        "pair": pair_address,
        "name": name
    })

    save_data(data)

    bot.send_message(
        message.chat.id,
        f"✅ Монета <b>{name}</b> добавлена\nТекущая цена: ${price}"
    )

# ------------------ MY COINS ------------------

def show_my_coins(message):
    data = load_data()
    user_id = str(message.chat.id)

    if user_id not in data or not data[user_id]:
        bot.send_message(message.chat.id, "У тебя нет добавленных монет.")
        return

    markup = types.InlineKeyboardMarkup()

    for i, coin in enumerate(data[user_id]):
        coin_id = f"{message.chat.id}_{i}"

        # сохраняем данные в память
        callback_storage[coin_id] = coin

        markup.add(
            types.InlineKeyboardButton(
                coin["name"],
                callback_data=f"coin_{coin_id}"
            )
        )

    bot.send_message(message.chat.id, "📊 Твои монеты:", reply_markup=markup)

# ------------------ RUN ------------------

bot.infinity_polling()
