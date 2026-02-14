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

def coin_keyboard(pair):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📈 Цена %", callback_data=f"setprice:{pair}"),
        InlineKeyboardButton("⚡ Спред %", callback_data=f"setmexc:{pair}")
    )
    kb.row(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del:{pair}")
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
        "🚀 *DEX MEME ENGINE BOT*\n\n"
        "*Функционал:*\n"
        "• 📈 Алерты по % изменения цены\n"
        "• ⚡ Алерты по спреду DEX ↔ MEXC\n"
        "• 🧠 Hybrid Market Engine\n"
        "• 🚀 Авто-сигнал импульса\n"
        "• 👥 Multi-user\n"
        "• 🛡 Anti-spam\n"
        "• ✅ Любые пары Dexscreener\n\n"
        "Добавь ссылку Dexscreener.",
        reply_markup=main_menu()
    )

# ================== CALLBACK ==================
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    uid = str(call.message.chat.id)
    user = get_user(uid)
    data = call.data

    if data.startswith("setprice:"):
        pair = data.split(":")[1]
        user_states[uid] = ("price", pair)
        bot.send_message(uid, "Введи % изменения цены:")
        return

    if data.startswith("setmexc:"):
        pair = data.split(":")[1]
        user_states[uid] = ("mexc", pair)
        bot.send_message(uid, "Введи % спреда DEX ↔ MEXC:")
        return

    if data.startswith("del:"):
        pair = data.split(":")[1]
        if pair in user["coins"]:
            del user["coins"][pair]
            save_db()
            bot.send_message(uid, "Монета удалена.")
        return

# ================== TEXT ==================
@bot.message_handler(func=lambda m: True)
def text_handler(m):

    if not m.text:
        return

    uid = str(m.chat.id)
    user = get_user(uid)

    # ==== SETTINGS INPUT ====
    if uid in user_states:
        mode, pair = user_states[uid]
        try:
            value = float(m.text)
            if mode == "price":
                user["coins"][pair]["alert"] = value
            else:
                user["coins"][pair]["mexc_alert"] = value
            save_db()
            bot.send_message(uid, "✅ Сохранено.")
        except:
            bot.send_message(uid, "Нужно число.")
        del user_states[uid]
        return

    # ==== ADD COIN BUTTON ====
    if "Добавить" in m.text:
        bot.send_message(uid, "Пришли ссылку Dexscreener.")
        return

    # ==== MY COINS BUTTON ====
    if "Мои монеты" in m.text:

        coins = user.get("coins", {})

        if not coins:
            bot.send_message(uid, "У тебя пока нет добавленных монет.")
            return

        for pair, coin in coins.items():

            symbol = coin.get("symbol", "Unknown")
            quote = coin.get("quote_symbol", "?")
            price_alert = coin.get("alert", "-")
            spread_alert = coin.get("mexc_alert", "-")
            score = coin.get("last_score", 0)

            bot.send_message(
                uid,
                f"*{symbol}/{quote}*\n"
                f"Цена alert: {price_alert}%\n"
                f"Спред alert: {spread_alert}%\n"
                f"Engine: {score}/100",
                reply_markup=coin_keyboard(pair)
            )

        return

    # ==== ADD BY LINK ====
    parsed = parse_dex_link(m.text)
    if parsed:
        chain, pair = parsed
        data = get_dex_data(chain, pair)
        if not data:
            bot.send_message(uid, "Пара не найдена.")
            return

        user["coins"][pair] = {
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

# ================== WATCHER ==================
def watcher():
    while True:
        for uid, u in DB.items():
            for pair, coin in u["coins"].items():

                data = get_dex_data(coin["chain"], pair)
                if not data:
                    continue

                price = data["price"]
                volume = data["volume"]
                mexc_price = get_mexc_price(coin["symbol"])

                # PRICE ALERT
                start_price = coin["start_price"]
                change = (price - start_price) / start_price * 100

                if abs(change) >= coin["alert"]:
                    bot.send_message(
                        uid,
                        f"📈 *PRICE ALERT — {coin['symbol']}*\nChange: {round(change,2)}%"
                    )
                    coin["start_price"] = price

                # SPREAD ALERT
                if coin.get("mexc_alert") and mexc_price:

                    spread = (mexc_price - price) / price * 100
                    threshold = coin["mexc_alert"]

                    if abs(spread) >= threshold and not coin["spread_triggered"]:

                        direction = "Long bias" if spread > 0 else "Short bias"

                        bot.send_message(
                            uid,
                            f"⚡ *SPREAD ALERT — {coin['symbol']}*\n"
                            f"DEX: ${price}\n"
                            f"MEXC: ${mexc_price}\n"
                            f"Spread: {round(spread,2)}%\n"
                            f"Bias: {direction}"
                        )

                        coin["spread_triggered"] = True

                    if abs(spread) < threshold * 0.7:
                        coin["spread_triggered"] = False

                # ENGINE
                coin["history"].append({
                    "price": price,
                    "volume": volume
                })

                if len(coin["history"]) > MAX_HISTORY:
                    coin["history"].pop(0)

                score = calculate_engine(coin, mexc_price)
                coin["last_score"] = score

                if score >= ENGINE_SIGNAL_THRESHOLD and not coin["engine_triggered"]:
                    bot.send_message(
                        uid,
                        f"🚀 *STRONG MOMENTUM — {coin['symbol']}*\nEngine Score: {score}/100"
                    )
                    coin["engine_triggered"] = True

                if score < ENGINE_RESET_THRESHOLD:
                    coin["engine_triggered"] = False

                save_db()

        time.sleep(CHECK_INTERVAL)

threading.Thread(target=watcher, daemon=True).start()
bot.remove_webhook()
bot.infinity_polling(skip_pending=True)
