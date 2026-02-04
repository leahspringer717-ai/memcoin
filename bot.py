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
    raise RuntimeError("BOT_TOKEN is missing")

BOT_TOKEN = BOT_TOKEN.strip()

bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "coins.json"
DEX_API = "https://api.dexscreener.com/latest/dex/pairs"
CHECK_INTERVAL = 300

# ================= STORAGE =================

if os.path.exists(DATA_FILE):
    with open(DATA_FILE) as f:
        USERS = json.load(f)
else:
    USERS = {}


def save():
    with open(DATA_FILE, "w") as f:
        json.dump(USERS, f, indent=2)


def get_user(uid):
    return USERS.setdefault(str(uid), {})

# ================= HELPERS =================

def extract_pair(text):
    m = re.search(r"dexscreener\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9x]+)", text)
    return m.groups() if m else (None, None)


def fetch_pair(chain, pair):
    url = f"{DEX_API}/{chain}/{pair}"
    r = requests.get(url, timeout=10)
    data = r.json()

    for p in data.get("pairs", []):
        if p.get("quoteToken", {}).get("symbol") in ("USDT", "USDC"):
            return p
    return None

# ================= UI =================

def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Add coin", callback_data="add"))
    kb.add(types.InlineKeyboardButton("📊 My coins", callback_data="list"))
    return kb

# ================= BOT =================

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🤖 *MemCoin Bot*\n\n"
        "• Track memcoins\n"
        "• USDT / USDC pairs\n"
        "• Pump alerts\n\n"
        "Use buttons below 👇",
        reply_markup=main_menu()
    )


@bot.callback_query_handler(func=lambda c: c.data == "add")
def add_prompt(c):
    bot.send_message(c.message.chat.id, "Send DexScreener link")


@bot.message_handler(func=lambda m: m.text and "dexscreener.com" in m.text)
def add_coin(m):
    chain, pair = extract_pair(m.text)
    if not chain:
        bot.reply_to(m, "Invalid link")
        return

    data = fetch_pair(chain, pair)
    if not data:
        bot.reply_to(m, "Pair not found or not USDT/USDC")
        return

    user = get_user(m.from_user.id)

    user[pair] = {
        "chain": chain,
        "symbol": data["baseToken"]["symbol"],
        "price": float(data["priceUsd"])
    }
    save()

    bot.reply_to(
        m,
        f"✅ {user[pair]['symbol']} added\nPrice: ${user[pair]['price']}"
    )


@bot.callback_query_handler(func=lambda c: c.data == "list")
def list_coins(c):
    user = get_user(c.from_user.id)
    if not user:
        bot.send_message(c.message.chat.id, "No coins yet")
        return

    for coin in user.values():
        bot.send_message(
            c.message.chat.id,
            f"{coin['symbol']} — ${coin['price']}"
        )

# ================= START =================

def start_bot():
    bot.remove_webhook()
    print("🤖 Bot started")
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    start_bot()
