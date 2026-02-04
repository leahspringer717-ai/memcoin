import os
import json
import time
import threading
import requests
import telebot
from telebot.types import *

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

DATA_FILE = "data.json"
CHECK_INTERVAL = 60  # сек
ALERT_WINDOW = 3600  # 1 час

# ================= STORAGE =================
def load():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save():
    with open(DATA_FILE, "w") as f:
        json.dump(DB, f, indent=2)

DB = load()

def user(uid):
    uid = str(uid)
    if uid not in DB:
        DB[uid] = {"coins": {}}
    return DB[uid]

# ================= UI =================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить монету")
    kb.add("📊 Мои монеты", "⚙️ Настройки")
    return kb

def coin_buttons(chain, pair, url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔗 DEX", url=url))
    kb.add(InlineKeyboardButton("⚙️ Алерт %", callback_data=f"alert:{chain}:{pair}"))
    kb.add(InlineKeyboardButton("❌ Удалить", callback_data=f"del:{chain}:{pair}"))
    return kb

# ================= START =================
@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🤖 *MemCoinScanner*\n\n"
        "• Любые сети (ETH / BSC / BASE / ARB)\n"
        "• Только USDT пары\n"
        "• Алерты > X% за 1 час\n\n"
        "Нажми ➕ *Добавить монету* и пришли ссылку DexScreener",
        reply_markup=main_menu()
    )

# ================= ADD FLOW =================
@bot.message_handler(func=lambda m: m.text == "➕ Добавить монету")
def ask_link(m):
    bot.send_message(m.chat.id, "🔗 Пришли ссылку DexScreener")

@bot.message_handler(func=lambda m: "dexscreener.com" in m.text.lower())
def add_coin(m):
    try:
        # Telegram preview-safe
        text = m.text.strip().split()[0]
        parts = text.split("/")

        chain = parts[-2]
        pair = parts[-1]

        api = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"
        r = requests.get(api, timeout=10).json()

        if "pair" not in r:
            bot.send_message(m.chat.id, "❌ Пара не найдена")
            return

        p = r["pair"]

        if p["quoteToken"]["symbol"] != "USDT":
            bot.send_message(m.chat.id, "❌ Поддерживаются только USDT пары")
            return

        u = user(m.chat.id)

        u["coins"][pair] = {
            "chain": chain,
            "symbol": p["baseToken"]["symbol"],
            "price": float(p["priceUsd"]),
            "last_price": float(p["priceUsd"]),
            "alert": 10,
            "last_alert": 0,
            "url": p["url"]
        }

        save()

        bot.send_message(
            m.chat.id,
            f"✅ *{p['baseToken']['symbol']}* добавлена\n"
            f"💰 ${p['priceUsd']}",
            reply_markup=coin_buttons(chain, pair, p["url"])
        )

    except Exception as e:
        bot.send_message(m.chat.id, "❌ Ошибка добавления монеты")

# ================= LIST =================
@bot.message_handler(func=lambda m: m.text == "📊 Мои монеты")
def my_coins(m):
    u = user(m.chat.id)
    if not u["coins"]:
        bot.send_message(m.chat.id, "Монет пока нет")
        return

    for pair, c in u["coins"].items():
        bot.send_message(
            m.chat.id,
            f"🪙 *{c['symbol']}*\n"
            f"Алерт: {c['alert']}%",
            reply_markup=coin_buttons(c["chain"], pair, c["url"])
        )

# ================= CALLBACKS =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("del"))
def delete_coin(c):
    _, chain, pair = c.data.split(":")
    u = user(c.message.chat.id)
    if pair in u["coins"]:
        del u["coins"][pair]
        save()
        bot.edit_message_text("❌ Монета удалена", c.message.chat.id, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("alert"))
def change_alert(c):
    msg = bot.send_message(c.message.chat.id, "✏️ Введи процент алерта (например 15)")
    bot.register_next_step_handler(msg, set_alert, c.data)

def set_alert(m, data):
    try:
        _, chain, pair = data.split(":")
        value = float(m.text)
        u = user(m.chat.id)
        u["coins"][pair]["alert"] = value
        save()
        bot.send_message(m.chat.id, f"✅ Алерт установлен: {value}%")
    except:
        bot.send_message(m.chat.id, "❌ Неверное значение")

# ================= WATCHER =================
def watcher():
    while True:
        for uid, u in DB.items():
            for pair, c in u["coins"].items():
                try:
                    api = f"https://api.dexscreener.com/latest/dex/pairs/{c['chain']}/{pair}"
                    r = requests.get(api, timeout=10).json()
                    if "pair" not in r:
                        continue

                    price = float(r["pair"]["priceUsd"])
                    diff = ((price - c["last_price"]) / c["last_price"]) * 100
                    now = time.time()

                    if abs(diff) >= c["alert"] and now - c["last_alert"] >= ALERT_WINDOW:
                        bot.send_message(
                            uid,
                            f"🚨 *{c['symbol']}*\n"
                            f"Цена: ${price:.6f}\n"
                            f"Изм: {diff:.2f}%",
                            reply_markup=coin_buttons(c["chain"], pair, c["url"])
                        )
                        c["last_alert"] = now
                        c["last_price"] = price
                        save()
                except:
                    pass
        time.sleep(CHECK_INTERVAL)

threading.Thread(target=watcher, daemon=True).start()
bot.infinity_polling(skip_pending=True)
