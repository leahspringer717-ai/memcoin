import os
import re
import json
import time
import threading
import requests
import telebot
from telebot import types

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

BOT_TOKEN = BOT_TOKEN.strip()

DEX_API = "https://api.dexscreener.com/latest/dex/pairs"
DATA_FILE = "coins.json"

CHECK_INTERVAL = 300  # 5 минут

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ================= STORAGE =================

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        tracked = json.load(f)
else:
    tracked = {}


def save():
    with open(DATA_FILE, "w") as f:
        json.dump(tracked, f, indent=2)


# ================= UI =================

def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Добавить монету", callback_data="add"))
    kb.add(types.InlineKeyboardButton("📊 Мои монеты", callback_data="list"))
    return kb


def coin_menu(pair):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("⚙️ Алерты", callback_data=f"alert:{pair}"),
        types.InlineKeyboardButton("🗑 Удалить", callback_data=f"del:{pair}")
    )
    return kb


def alert_menu(pair):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("5%", callback_data=f"pct:{pair}:5"),
        types.InlineKeyboardButton("10%", callback_data=f"pct:{pair}:10"),
        types.InlineKeyboardButton("20%", callback_data=f"pct:{pair}:20")
    )
    kb.add(
        types.InlineKeyboardButton("⏱ 15м", callback_data=f"tf:{pair}:900"),
        types.InlineKeyboardButton("⏱ 1ч", callback_data=f"tf:{pair}:3600")
    )
    kb.add(types.InlineKeyboardButton("🔕 Вкл/Выкл", callback_data=f"toggle:{pair}"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="list"))
    return kb


# ================= HELPERS =================

def extract(url):
    m = re.search(r"dexscreener\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9x]+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def fetch(chain, pair):
    r = requests.get(f"{DEX_API}/{chain}/{pair}", timeout=10)
    for p in r.json().get("pairs", []):
        if p.get("quoteToken", {}).get("symbol") == "USDT":
            return p
    return None


# ================= BOT =================

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *MemCoin Scanner*\n\n"
        "Inline-интерфейс\n"
        "Персональные алерты\n\n"
        "Выбери действие 👇",
        reply_markup=main_menu()
    )


@bot.callback_query_handler(func=lambda c: c.data == "add")
def add_prompt(c):
    bot.answer_callback_query(c.id)
    bot.send_message(c.message.chat.id, "🔗 Пришли ссылку Dexscreener (USDT)")


@bot.message_handler(func=lambda m: m.text and "dexscreener.com" in m.text)
def add_coin(m):
    chain, pair = extract(m.text)
    data = fetch(chain, pair)

    if not data:
        bot.send_message(m.chat.id, "❌ Не найдена USDT пара")
        return

    price = float(data["priceUsd"])

    tracked[pair] = {
        "chat": m.chat.id,
        "chain": chain,
        "symbol": data["baseToken"]["symbol"],
        "name": data["baseToken"]["name"],
        "base_price": price,
        "last_check": time.time(),
        "pct": 10,
        "tf": 3600,
        "enabled": True
    }
    save()

    bot.send_message(
        m.chat.id,
        f"✅ *{tracked[pair]['symbol']}* добавлена",
        reply_markup=coin_menu(pair)
    )


@bot.callback_query_handler(func=lambda c: c.data == "list")
def list_coins(c):
    bot.answer_callback_query(c.id)
    if not tracked:
        bot.send_message(c.message.chat.id, "📊 Список пуст", reply_markup=main_menu())
        return

    for pair, coin in tracked.items():
        bot.send_message(
            c.message.chat.id,
            f"*{coin['symbol']}*\n"
            f"Алерт: {coin['pct']}% / {coin['tf']//60}м\n"
            f"{'🟢 Вкл' if coin['enabled'] else '🔴 Выкл'}",
            reply_markup=coin_menu(pair)
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("alert:"))
def alert_settings(c):
    pair = c.data.split(":")[1]
    bot.edit_message_reply_markup(
        c.message.chat.id,
        c.message.message_id,
        reply_markup=alert_menu(pair)
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("pct:"))
def set_pct(c):
    _, pair, pct = c.data.split(":")
    tracked[pair]["pct"] = int(pct)
    save()
    bot.answer_callback_query(c.id, f"Порог {pct}%")


@bot.callback_query_handler(func=lambda c: c.data.startswith("tf:"))
def set_tf(c):
    _, pair, tf = c.data.split(":")
    tracked[pair]["tf"] = int(tf)
    save()
    bot.answer_callback_query(c.id, "Таймфрейм обновлён")


@bot.callback_query_handler(func=lambda c: c.data.startswith("toggle:"))
def toggle(c):
    pair = c.data.split(":")[1]
    tracked[pair]["enabled"] = not tracked[pair]["enabled"]
    save()
    bot.answer_callback_query(c.id, "Переключено")


@bot.callback_query_handler(func=lambda c: c.data.startswith("del:"))
def delete(c):
    pair = c.data.split(":")[1]
    del tracked[pair]
    save()
    bot.edit_message_text("🗑 Монета удалена", c.message.chat.id, c.message.message_id)


# ================= WATCHER =================

def watcher():
    while True:
        now = time.time()
        for pair, c in tracked.items():
            if not c["enabled"]:
                continue

            data = fetch(c["chain"], pair)
            if not data:
                continue

            price = float(data["priceUsd"])
            change = ((price - c["base_price"]) / c["base_price"]) * 100

            if abs(change) >= c["pct"] and now - c["last_check"] >= c["tf"]:
                bot.send_message(
                    c["chat"],
                    f"{'📈 ПАМП' if change > 0 else '📉 ДАМП'} *{c['symbol']}*\n"
                    f"{change:.2f}% за {c['tf']//60}м\n"
                    f"Цена: ${price}"
                )
                c["base_price"] = price
                c["last_check"] = now
                save()

        time.sleep(CHECK_INTERVAL)


# ================= START =================

threading.Thread(target=watcher, daemon=True).start()

bot.remove_webhook()
print("🤖 Bot started")
bot.infinity_polling(skip_pending=True)
