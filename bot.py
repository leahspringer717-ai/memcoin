import os
import json
import time
import threading
import requests
import telebot
from telebot.types import *

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

DATA_FILE = "data.json"
CHECK_INTERVAL = 60

# ================= STORAGE =================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

data = load_data()

def user(uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"coins": {}}
    return data[uid]

# ================= UI =================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить монету")
    kb.add("📊 Мои монеты")
    return kb

def coin_kb(addr, chain, url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔗 Dex", url=url))
    kb.add(InlineKeyboardButton("❌ Удалить", callback_data=f"del:{chain}:{addr}"))
    return kb

# ================= START =================
@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🤖 *MemCoin Scanner*\n\n"
        "• Алерты по %\n"
        "• Детект пампа\n"
        "• Anti-rug (скоро)\n\n"
        "Нажми ➕ Добавить монету и пришли ссылку DexScreener",
        reply_markup=main_menu()
    )

# ================= ADD =================
@bot.message_handler(func=lambda m: m.text == "➕ Добавить монету")
def ask(m):
    bot.send_message(m.chat.id, "🔗 Пришли ссылку DexScreener")

@bot.message_handler(func=lambda m: "dexscreener.com" in m.text.lower())
def add_coin(m):
    try:
        parts = m.text.strip().split("/")
        chain = parts[-2]
        pair = parts[-1]

        api = f"https://api.dexscreener.com/latest/dex/pairs/{chain}/{pair}"
        r = requests.get(api, timeout=10).json()

        if "pair" not in r:
            bot.send_message(m.chat.id, "❌ Пара не найдена")
            return

        p = r["pair"]

        if p["quoteToken"]["symbol"] != "USDT":
            bot.send_message(m.chat.id, "❌ Только USDT пары")
            return

        u = user(m.chat.id)
        u["coins"][pair] = {
            "chain": chain,
            "symbol": p["baseToken"]["symbol"],
            "price": float(p["priceUsd"]),
            "url": p["url"],
            "alert": 10,
            "last": float(p["priceUsd"]),
            "last_alert": 0
        }

        save_data(data)

        bot.send_message(
            m.chat.id,
            f"✅ *{p['baseToken']['symbol']}* добавлена\n"
            f"💰 ${p['priceUsd']}",
            reply_markup=coin_kb(pair, chain, p["url"])
        )

    except Exception as e:
        bot.send_message(m.chat.id, f"❌ Ошибка добавления")

# ================= LIST =================
@bot.message_handler(func=lambda m: m.text == "📊 Мои монеты")
def list_coins(m):
    u = user(m.chat.id)
    if not u["coins"]:
        bot.send_message(m.chat.id, "Монет нет")
        return

    for addr, c in u["coins"].items():
        bot.send_message(
            m.chat.id,
            f"🪙 *{c['symbol']}*\nАлерт: {c['alert']}%",
            reply_markup=coin_kb(addr, c["chain"], c["url"])
        )

# ================= DELETE =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("del"))
def delete(c):
    _, chain, addr = c.data.split(":")
    u = user(c.message.chat.id)
    if addr in u["coins"]:
        del u["coins"][addr]
        save_data(data)
        bot.edit_message_text("❌ Удалено", c.message.chat.id, c.message.message_id)

# ================= WATCHER =================
def watcher():
    while True:
        for uid, u in data.items():
            for addr, c in u["coins"].items():
                try:
                    api = f"https://api.dexscreener.com/latest/dex/pairs/{c['chain']}/{addr}"
                    r = requests.get(api, timeout=10).json()
                    if "pair" not in r:
                        continue

                    price = float(r["pair"]["priceUsd"])
                    diff = ((price - c["last"]) / c["last"]) * 100
                    now = time.time()

                    if abs(diff) >= c["alert"] and now - c["last_alert"] > 3600:
                        bot.send_message(
                            uid,
                            f"🚨 *{c['symbol']}*\n"
                            f"Цена: ${price:.6f}\n"
                            f"Изм: {diff:.2f}%",
                            reply_markup=coin_kb(addr, c["chain"], c["url"])
                        )
                        c["last_alert"] = now
                        c["last"] = price
                        save_data(data)

                except:
                    pass
        time.sleep(CHECK_INTERVAL)

threading.Thread(target=watcher, daemon=True).start()
bot.infinity_polling(skip_pending=True)
