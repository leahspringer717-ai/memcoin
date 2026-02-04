import os
import json
import time
import threading
import requests
import telebot

from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

BOT_TOKEN = BOT_TOKEN.strip()
DATA_FILE = "data.json"
CHECK_INTERVAL = 60  # секунд

DEX_API = "https://api.dexscreener.com/latest/dex/pairs"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

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

def user_data(uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = {"coins": {}}
    return data[uid]

# ================= UI =================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить монету"))
    kb.add(KeyboardButton("📊 Мои монеты"), KeyboardButton("⚙️ Настройки"))
    return kb

def coin_keyboard(addr, url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔗 Открыть DEX", url=url))
    kb.add(
        InlineKeyboardButton("⚙️ Алерты", callback_data=f"alerts:{addr}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete:{addr}")
    )
    return kb

# ================= START =================
@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(
        msg.chat.id,
        "🤖 *MemCoinScanner*\n\n"
        "Я умею:\n"
        "• 📈 Алерты роста/падения за 1 час\n"
        "• 🚀 Детект пампа\n"
        "• ⚠️ Anti-rug риск\n"
        "• 💾 Сохранять твои монеты\n\n"
        "Нажми «Добавить монету» и пришли ссылку DexScreener 👇",
        reply_markup=main_menu()
    )

# ================= ADD COIN =================
@bot.message_handler(func=lambda m: m.text == "➕ Добавить монету")
def ask_link(msg):
    bot.send_message(msg.chat.id, "🔗 Пришли ссылку DexScreener")

@bot.message_handler(func=lambda m: "dexscreener.com" in m.text.lower())
def process_link(msg):
    try:
        url = msg.text.strip()
        pair_address = url.rstrip("/").split("/")[-1]

        r = requests.get(f"{DEX_API}/{pair_address}", timeout=10).json()

        if "pairs" not in r or not r["pairs"]:
            bot.send_message(msg.chat.id, "❌ Пара не найдена")
            return

        pair = r["pairs"][0]

        if pair["quoteToken"]["symbol"] != "USDT":
            bot.send_message(msg.chat.id, "❌ Только пары с USDT")
            return

        u = user_data(msg.chat.id)

        u["coins"][pair_address] = {
            "symbol": pair["baseToken"]["symbol"],
            "name": pair["baseToken"]["name"],
            "url": pair["url"],
            "alert_up": 10,
            "alert_down": 10,
            "last_price": float(pair["priceUsd"]),
            "last_alert": 0,
        }

        save_data(data)

        bot.send_message(
            msg.chat.id,
            f"✅ *{pair['baseToken']['symbol']}* добавлена\n"
            f"💰 Цена: ${pair['priceUsd']}",
            reply_markup=coin_keyboard(pair_address, pair["url"])
        )

    except Exception as e:
        bot.send_message(msg.chat.id, "❌ Не удалось добавить монету")

# ================= LIST =================
@bot.message_handler(func=lambda m: m.text == "📊 Мои монеты")
def list_coins(msg):
    u = user_data(msg.chat.id)

    if not u["coins"]:
        bot.send_message(msg.chat.id, "Пока нет добавленных монет")
        return

    for addr, c in u["coins"].items():
        bot.send_message(
            msg.chat.id,
            f"🪙 *{c['symbol']}*\n"
            f"📈 Алерт вверх: {c['alert_up']}%\n"
            f"📉 Алерт вниз: {c['alert_down']}%",
            reply_markup=coin_keyboard(addr, c["url"])
        )

# ================= CALLBACKS =================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.message.chat.id
    u = user_data(uid)

    if call.data.startswith("delete:"):
        addr = call.data.split(":")[1]
        if addr in u["coins"]:
            del u["coins"][addr]
            save_data(data)
            bot.edit_message_text("❌ Монета удалена", uid, call.message.message_id)

    if call.data.startswith("alerts:"):
        addr = call.data.split(":")[1]
        kb = InlineKeyboardMarkup()
        for p in [5, 10, 20]:
            kb.add(
                InlineKeyboardButton(f"📈 +{p}%", callback_data=f"up:{addr}:{p}"),
                InlineKeyboardButton(f"📉 -{p}%", callback_data=f"down:{addr}:{p}")
            )
        bot.edit_message_text(
            "⚙️ Настрой алерты:",
            uid,
            call.message.message_id,
            reply_markup=kb
        )

    if call.data.startswith("up:") or call.data.startswith("down:"):
        t, addr, val = call.data.split(":")
        if addr in u["coins"]:
            if t == "up":
                u["coins"][addr]["alert_up"] = int(val)
            else:
                u["coins"][addr]["alert_down"] = int(val)
            save_data(data)
            bot.answer_callback_query(call.id, "✅ Сохранено")

# ================= WATCHER =================
def watcher():
    while True:
        try:
            for uid, u in data.items():
                for addr, c in u["coins"].items():
                    r = requests.get(f"{DEX_API}/{addr}", timeout=10).json()
                    if "pairs" not in r or not r["pairs"]:
                        continue

                    pair = r["pairs"][0]
                    price = float(pair["priceUsd"])
                    old = c["last_price"]

                    change = ((price - old) / old) * 100
                    now = time.time()

                    if now - c["last_alert"] < 3600:
                        continue

                    if change >= c["alert_up"] or change <= -c["alert_down"]:
                        bot.send_message(
                            uid,
                            f"🚨 *АЛЕРТ {c['symbol']}*\n"
                            f"💰 Цена: ${price:.6f}\n"
                            f"📊 Изм. за 1ч: {change:.2f}%",
                            reply_markup=coin_keyboard(addr, c["url"])
                        )
                        c["last_alert"] = now
                        c["last_price"] = price
                        save_data(data)

        except Exception:
            pass

        time.sleep(CHECK_INTERVAL)

# ================= RUN =================
threading.Thread(target=watcher, daemon=True).start()
bot.infinity_polling(skip_pending=True)
