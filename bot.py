import telebot
from telebot.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
import requests
import time
import json
import os
import threading
import uuid   # <<< ДОБАВЛЕНО

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

DATA_FILE = "data.json"
CHECK_INTERVAL = 60
ENGINE_SIGNAL_THRESHOLD = 75
ENGINE_RESET_THRESHOLD = 60
MAX_HISTORY = 30
# ============================================

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="Markdown",
    disable_web_page_preview=True
)

lock = threading.Lock()
user_states = {}

# ================== STORAGE ==================
def load_db():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_db():
    with lock:
        with open(DATA_FILE, "w") as f:
            json.dump(DB, f, indent=2)

DB = load_db()

def get_user(uid):
    uid = str(uid)
    if uid not in DB:
        DB[uid] = {"coins": {}}
        save_db()
    return DB[uid]

# ================== UI ==================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(
        KeyboardButton("➕ Добавить монету"),
        KeyboardButton("📂 Мои монеты")
    )
    kb.row(KeyboardButton("ℹ️ О боте"))
    return kb

# ⚠️ ИСПРАВЛЕНО — теперь используем coin_id
def coin_keyboard(coin_id):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📈 Цена %", callback_data=f"sp:{coin_id}"),
        InlineKeyboardButton("⚡ Спред %", callback_data=f"sm:{coin_id}")
    )
    kb.row(
        InlineKeyboardButton("❌ Удалить", callback_data=f"dl:{coin_id}")
    )
    return kb

# ================== DEX ==================
def parse_dex_link(text):
    if "dexscreener.com" not in text:
        return None
    parts = text.strip().split("/")
    try:
        return parts[3], parts[4]
    except:
        return None

def get_dex_data(chain, pair):
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}",
            timeout=10
        ).json()

        p = r.get("pair")
        if not p:
            return None

        return {
            "symbol": p["baseToken"]["symbol"],
            "price": float(p["priceUsd"]),
            "volume": float(p.get("volume", {}).get("h24", 0)),
            "quote_symbol": p["quoteToken"]["symbol"],
            "url": p["url"]
        }
    except:
        return None

# ================== MEXC ==================
def get_mexc_price(symbol):
    try:
        r = requests.get(
            "https://contract.mexc.com/api/v1/contract/ticker",
            timeout=10
        ).json()

        pair = f"{symbol}USDT"
        for i in r.get("data", []):
            if i["symbol"] == pair:
                return float(i["lastPrice"])
    except:
        pass
    return None

# ================== ENGINE ==================
def calculate_engine(coin, mexc_price):
    history = coin["history"]
    if len(history) < 6:
        return 0

    price_now = history[-1]["price"]
    price_30m_ago = history[0]["price"]
    price_5m_ago = history[-6]["price"]

    vol_now = history[-1]["volume"]
    vol_old = history[0]["volume"]

    trend = (price_now - price_30m_ago) / price_30m_ago * 100
    trend_score = 40 if trend > 10 else 30 if trend > 5 else 20 if trend > 2 else 10 if trend > 0 else 0

    accel = (price_now - price_5m_ago) / price_5m_ago * 100
    accel_score = min(max(accel * 4, 0), 20)

    vol_change = (vol_now - vol_old) / vol_old * 100 if vol_old > 0 else 0
    vol_score = 20 if trend > 0 and vol_change > 10 else 15 if trend > 0 else 0

    if mexc_price:
        spread = (mexc_price - price_now) / price_now * 100
        fut_score = 20 if spread > 2 else 15 if spread > 1 else 10 if spread > 0 else 0
    else:
        fut_score = 0

    return round(trend_score + accel_score + vol_score + fut_score, 2)

# ================== START ==================
@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *DEX MEME ENGINE BOT*",
        reply_markup=main_menu()
    )

# ================== МОИ МОНЕТЫ ==================
@bot.message_handler(func=lambda m: m.text and "Мои монеты" in m.text)
def my_coins(m):
    uid = str(m.chat.id)
    user = get_user(uid)

    coins = user.get("coins", {})

    if not coins:
        bot.send_message(uid, "У тебя пока нет добавленных монет.")
        return

    for coin_id, coin in coins.items():
        bot.send_message(
            uid,
            f"*{coin.get('symbol','?')}/{coin.get('quote_symbol','?')}*\n"
            f"Цена alert: {coin.get('alert','-')}%\n"
            f"Спред alert: {coin.get('mexc_alert','-')}%\n"
            f"Engine: {coin.get('last_score',0)}/100",
            reply_markup=coin_keyboard(coin_id)
        )

# ================== ДОБАВЛЕНИЕ ==================
@bot.message_handler(func=lambda m: m.text and "dexscreener.com" in m.text)
def add_by_link(m):
    uid = str(m.chat.id)
    user = get_user(uid)

    parsed = parse_dex_link(m.text)
    if not parsed:
        bot.send_message(uid, "Неверная ссылка.")
        return

    chain, pair = parsed
    data = get_dex_data(chain, pair)

    if not data:
        bot.send_message(uid, "Пара не найдена.")
        return

    coin_id = uuid.uuid4().hex[:8]  # <<< КОРОТКИЙ ID

    user["coins"][coin_id] = {
        "pair": pair,
        "symbol": data["symbol"],
        "quote_symbol": data["quote_symbol"],
        "chain": chain,
        "start_price": data["price"],
        "alert": 10,
        "mexc_alert": None,
        "history": [],
        "engine_triggered": False,
        "spread_triggered": False,
        "last_score": 0
    }

    save_db()
    bot.send_message(uid, f"✅ {data['symbol']}/{data['quote_symbol']} добавлена.")

# ================== CALLBACK ==================
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = str(call.message.chat.id)
    user = get_user(uid)
    data = call.data

    if data.startswith("sp:"):
        coin_id = data.split(":")[1]
        user_states[uid] = ("price", coin_id)
        bot.send_message(uid, "Введи % изменения цены:")
        return

    if data.startswith("sm:"):
        coin_id = data.split(":")[1]
        user_states[uid] = ("mexc", coin_id)
        bot.send_message(uid, "Введи % спреда:")
        return

    if data.startswith("dl:"):
        coin_id = data.split(":")[1]
        if coin_id in user["coins"]:
            del user["coins"][coin_id]
            save_db()
            bot.send_message(uid, "Монета удалена.")
        return
