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
# ============================================

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="Markdown",
    disable_web_page_preview=True
)

lock = threading.Lock()

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
    kb.row(
        KeyboardButton("⚙️ Настройки"),
        KeyboardButton("ℹ️ О боте")
    )
    return kb

def coin_keyboard(pair, coin):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📊 DEX", url=coin["dex_url"]),
        InlineKeyboardButton("📈 Цена %", callback_data=f"price:{pair}")
    )
    kb.row(
        InlineKeyboardButton("⚡ MEXC %", callback_data=f"mexc:{pair}"),
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
        if not p["quoteToken"]["symbol"].upper().endswith("USDT"):
            return None
        return {
            "symbol": p["baseToken"]["symbol"],
            "price": float(p["priceUsd"]),
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

# ================== START ==================
@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *DEX / MEME ALERT BOT*\n\n"
        "• Мгновенные алерты по %\n"
        "• Отдельно: цена и DEX↔MEXC\n"
        "• Только USDT пары\n\n"
        "📌 Добавь монету ссылкой Dexscreener",
        reply_markup=main_menu()
    )

# ================== TEXT HANDLER ==================
@bot.message_handler(func=lambda m: True)
def text_handler(m):
    u = get_user(m.chat.id)

    if m.text == "➕ Добавить монету":
        bot.send_message(m.chat.id, "🔗 Пришли ссылку Dexscreener", reply_markup=main_menu())
        return

    if m.text == "📂 Мои монеты":
        if not u["coins"]:
            bot.send_message(m.chat.id, "📭 Монет нет", reply_markup=main_menu())
            return

        bot.send_message(
            m.chat.id,
            f"📂 Добавлено монет: *{len(u['coins'])}*",
            reply_markup=main_menu()
        )

        for pair, coin in u["coins"].items():
            mexc_txt = f"{coin['mexc_alert']}%" if coin["mexc_alert"] else "—"

            text = (
                f"*{coin['symbol']}*\n"
                f"Цена: ${coin['last_price']}\n"
                f"📈 Цена: {coin['alert']}%\n"
                f"⚡ DEX↔MEXC: {mexc_txt}"
            )

            bot.send_message(
                m.chat.id,
                text,
                reply_markup=coin_keyboard(pair, coin)
            )
        return

    parsed = parse_dex_link(m.text)
    if not parsed:
        return

    chain, pair = parsed
    data = get_dex_data(chain, pair)
    if not data:
        bot.send_message(m.chat.id, "❌ Нужна USDT пара")
        return

    u["coins"][pair] = {
        "symbol": data["symbol"],
        "chain": chain,
        "last_price": data["price"],
        "alert": 10,                 # 👈 price alert %
        "mexc_alert": None,
        "price_triggered": False,
        "mexc_triggered": False,
        "dex_url": data["url"]
    }
    save_db()

    bot.send_message(
        m.chat.id,
        f"✅ *{data['symbol']} добавлена*\nЦена: ${data['price']}",
        reply_markup=coin_keyboard(pair, u["coins"][pair])
    )

# ================== CALLBACKS ==================
@bot.callback_query_handler(func=lambda c: c.data.startswith("del:"))
def delete_coin(c):
    pair = c.data.split(":")[1]
    u = get_user(c.message.chat.id)
    if pair in u["coins"]:
        del u["coins"][pair]
        save_db()
    bot.edit_message_text("❌ Монета удалена", c.message.chat.id, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("price:"))
def ask_price_alert(c):
    msg = bot.send_message(c.message.chat.id, "📈 Введи % для алерта цены")
    bot.register_next_step_handler(msg, save_price_alert, c.data)

def save_price_alert(m, data):
    pair = data.split(":")[1]
    u = get_user(m.chat.id)
    try:
        u["coins"][pair]["alert"] = float(m.text)
        u["coins"][pair]["price_triggered"] = False
        save_db()
        bot.send_message(m.chat.id, "✅ Алерт цены обновлён", reply_markup=main_menu())
    except:
        bot.send_message(m.chat.id, "❌ Введи число")

@bot.callback_query_handler(func=lambda c: c.data.startswith("mexc:"))
def mexc_prompt(c):
    msg = bot.send_message(c.message.chat.id, "⚡ Введи % для DEX ↔ MEXC")
    bot.register_next_step_handler(msg, save_mexc, c.data)

def save_mexc(m, data):
    pair = data.split(":")[1]
    u = get_user(m.chat.id)
    try:
        u["coins"][pair]["mexc_alert"] = float(m.text)
        u["coins"][pair]["mexc_triggered"] = False
        save_db()
        bot.send_message(m.chat.id, "✅ MEXC алерт обновлён", reply_markup=main_menu())
    except:
        bot.send_message(m.chat.id, "❌ Введи число")

# ================== WATCHER ==================
def watcher():
    while True:
        for uid, u in DB.items():
            for pair, coin in u["coins"].items():
                data = get_dex_data(coin["chain"], pair)
                if not data:
                    continue

                old = coin["last_price"]
                new = data["price"]
                coin["last_price"] = new

                change = (new - old) / old * 100

                if abs(change) >= coin["alert"] and not coin["price_triggered"]:
                    bot.send_message(
                        uid,
                        f"🚨 *{coin['symbol']}*\nИзменение: {change:+.2f}%\nЦена: ${new}"
                    )
                    coin["price_triggered"] = True

                if abs(change) < coin["alert"]:
                    coin["price_triggered"] = False

                if coin["mexc_alert"]:
                    mexc = get_mexc_price(coin["symbol"])
                    if mexc:
                        spread = (mexc - new) / new * 100
                        if abs(spread) >= coin["mexc_alert"] and not coin["mexc_triggered"]:
                            bot.send_message(
                                uid,
                                f"⚡ *DEX ↔ MEXC*\n{coin['symbol']}\n"
                                f"DEX: ${new}\nMEXC: ${mexc}\n"
                                f"Спред: {spread:+.2f}%"
                            )
                            coin["mexc_triggered"] = True

                        if abs(spread) < coin["mexc_alert"]:
                            coin["mexc_triggered"] = False

                save_db()
        time.sleep(CHECK_INTERVAL)

# ================== START ==================
threading.Thread(target=watcher, daemon=True).start()
bot.remove_webhook()
bot.infinity_polling(skip_pending=True)
