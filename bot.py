import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
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

PRICE_CHECK_INTERVAL = 60          # секунд
ALERT_COOLDOWN = 1800              # 30 мин
# ============================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown", disable_web_page_preview=True)

lock = threading.Lock()

# ================== STORAGE ==================
def load():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save():
    with lock:
        with open(DATA_FILE, "w") as f:
            json.dump(DB, f, indent=2)

DB = load()

def user(uid):
    uid = str(uid)
    if uid not in DB:
        DB[uid] = {"coins": {}}
        save()
    return DB[uid]

# ================== DEX ==================
def parse_dex_link(text):
    if "dexscreener.com" not in text:
        return None

    parts = text.split("/")
    try:
        chain = parts[3]
        pair = parts[4]
        return chain, pair
    except:
        return None

def get_dex_data(chain, pair):
    url = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"
    r = requests.get(url, timeout=10).json()
    if not r.get("pair"):
        return None

    p = r["pair"]
    if not p["quoteToken"]["symbol"].upper().endswith("USDT"):
        return None

    return {
        "symbol": p["baseToken"]["symbol"],
        "price": float(p["priceUsd"]),
        "volume": p["volume"]["h1"],
        "change": p["priceChange"]["h1"],
        "dex": p["dexId"],
        "url": p["url"]
    }

# ================== MEXC ==================
def get_mexc_price(symbol):
    try:
        url = "https://contract.mexc.com/api/v1/contract/ticker"
        r = requests.get(url, timeout=10).json()
        pair = f"{symbol}USDT"
        for i in r.get("data", []):
            if i["symbol"] == pair:
                return float(i["lastPrice"])
    except:
        pass
    return None

# ================== UI ==================
def coin_keyboard(pair, coin):
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("📊 DEX", url=coin["dex_url"]),
        InlineKeyboardButton("⚡ MEXC %", callback_data=f"mexc:{pair}")
    )
    kb.row(
        InlineKeyboardButton("❌ Удалить", callback_data=f"del:{pair}")
    )
    return kb

# ================== COMMANDS ==================
@bot.message_handler(commands=["start"])
def start(m):
    text = (
        "🚀 *MEME / DEX ALERT BOT*\n\n"
        "Что умею:\n"
        "• Добавление монет через Dexscreener\n"
        "• Только пары USDT\n"
        "• Алерты > X% за 1 час\n"
        "• DEX ↔ MEXC Futures спред\n"
        "• Anti-spam\n"
        "• Multi-user\n\n"
        "📌 Просто пришли ссылку с Dexscreener"
    )
    bot.send_message(m.chat.id, text)

@bot.message_handler(func=lambda m: True)
def add_coin(m):
    parsed = parse_dex_link(m.text)
    if not parsed:
        return

    chain, pair = parsed
    data = get_dex_data(chain, pair)
    if not data:
        bot.send_message(m.chat.id, "❌ Не удалось получить данные (нужна пара USDT)")
        return

    u = user(m.chat.id)
    u["coins"][pair] = {
        "symbol": data["symbol"],
        "chain": chain,
        "last_price": data["price"],
        "alert": 10,
        "mexc_alert": None,
        "last_alert": 0,
        "last_mexc": 0,
        "dex_url": data["url"]
    }
    save()

    bot.send_message(
        m.chat.id,
        f"✅ *{data['symbol']} добавлена*\nЦена: ${data['price']}",
        reply_markup=coin_keyboard(pair, u["coins"][pair])
    )

# ================== CALLBACKS ==================
@bot.callback_query_handler(func=lambda c: c.data.startswith("del:"))
def delete_coin(c):
    pair = c.data.split(":")[1]
    u = user(c.message.chat.id)
    if pair in u["coins"]:
        del u["coins"][pair]
        save()
    bot.edit_message_text("❌ Монета удалена", c.message.chat.id, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("mexc:"))
def ask_mexc(c):
    msg = bot.send_message(c.message.chat.id, "Введи % для алерта DEX ↔ MEXC")
    bot.register_next_step_handler(msg, save_mexc, c.data)

def save_mexc(m, data):
    pair = data.split(":")[1]
    u = user(m.chat.id)
    try:
        u["coins"][pair]["mexc_alert"] = float(m.text)
        save()
        bot.send_message(m.chat.id, "⚡ MEXC алерт включён")
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

                now = time.time()

                change = (new - old) / old * 100
                if abs(change) >= coin["alert"] and now - coin["last_alert"] > ALERT_COOLDOWN:
                    bot.send_message(
                        uid,
                        f"🚨 *{coin['symbol']}*\nИзменение за 1ч: {change:+.2f}%\nЦена: ${new}"
                    )
                    coin["last_alert"] = now

                if coin["mexc_alert"]:
                    mexc = get_mexc_price(coin["symbol"])
                    if mexc:
                        spread = (mexc - new) / new * 100
                        if abs(spread) >= coin["mexc_alert"] and now - coin["last_mexc"] > ALERT_COOLDOWN:
                            bot.send_message(
                                uid,
                                f"⚡ *DEX ↔ MEXC*\n{coin['symbol']}\n"
                                f"DEX: ${new}\nMEXC: ${mexc}\nСпред: {spread:+.2f}%"
                            )
                            coin["last_mexc"] = now

                save()
        time.sleep(PRICE_CHECK_INTERVAL)

# ================== START ==================
threading.Thread(target=watcher, daemon=True).start()
bot.infinity_polling(skip_pending=True)
