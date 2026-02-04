import os
import re
import json
import time
import threading
import requests
import telebot
from telebot import types

# ================== CONFIG ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

BOT_TOKEN = BOT_TOKEN.strip()

DEX_API = "https://api.dexscreener.com/latest/dex/pairs"
DATA_FILE = "coins.json"

PRICE_CHECK_INTERVAL = 300   # 5 минут
ALERT_THRESHOLD = 10         # %

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ================== STORAGE ==================

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        tracked = json.load(f)
else:
    tracked = {}


def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(tracked, f, indent=2)


# ================== UI ==================

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Добавить монету")
    kb.add("📊 Мои монеты")
    return kb


# ================== HELPERS ==================

def extract_chain_pair(url):
    m = re.search(r"dexscreener\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9x]+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def fetch_pair(chain, pair):
    r = requests.get(f"{DEX_API}/{chain}/{pair}", timeout=10)
    data = r.json().get("pairs", [])
    for p in data:
        if p.get("quoteToken", {}).get("symbol") == "USDT":
            return p
    return None


def calc_risk(pair):
    risk = 0
    if pair.get("liquidity", {}).get("usd", 0) < 20000:
        risk += 40
    if pair.get("fdv", 0) > pair.get("liquidity", {}).get("usd", 1) * 20:
        risk += 30
    if pair.get("priceChange", {}).get("h1", 0) > 50:
        risk += 20
    return min(100, risk)


def anti_rug(pair):
    score = 100
    if pair.get("liquidity", {}).get("usd", 0) < 10000:
        score -= 40
    if pair.get("fdv", 0) == 0:
        score -= 20
    if pair.get("txns", {}).get("h1", {}).get("buys", 0) < 5:
        score -= 20
    return max(0, score)


# ================== BOT HANDLERS ==================

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *MemCoin Scanner*\n\n"
        "• алерты ±10% за 1ч\n"
        "• детект пампов\n"
        "• риск манипуляций\n"
        "• anti-rug скоринг\n\n"
        "Добавь монету 👇",
        reply_markup=main_menu()
    )


@bot.message_handler(func=lambda m: m.text == "➕ Добавить монету")
def add_prompt(m):
    bot.send_message(m.chat.id, "🔗 Пришли ссылку Dexscreener (USDT)")


@bot.message_handler(func=lambda m: m.text and "dexscreener.com" in m.text)
def add_coin(m):
    chain, pair = extract_chain_pair(m.text)
    if not chain:
        bot.send_message(m.chat.id, "❌ Неверная ссылка", reply_markup=main_menu())
        return

    data = fetch_pair(chain, pair)
    if not data:
        bot.send_message(m.chat.id, "❌ Нет USDT пары", reply_markup=main_menu())
        return

    symbol = data["baseToken"]["symbol"]
    name = data["baseToken"]["name"]
    price = float(data["priceUsd"])

    tracked[pair] = {
        "chat_id": m.chat.id,
        "chain": chain,
        "symbol": symbol,
        "name": name,
        "price_1h": price,
        "last_price": price,
        "added": time.time()
    }
    save_data()

    bot.send_message(
        m.chat.id,
        f"✅ *{symbol}* ({name}) добавлена\n"
        f"💰 Цена: `${price}`",
        reply_markup=main_menu()
    )


@bot.message_handler(func=lambda m: m.text == "📊 Мои монеты")
def my_coins(m):
    if not tracked:
        bot.send_message(m.chat.id, "📊 Список пуст", reply_markup=main_menu())
        return

    text = "📊 *Отслеживаемые монеты:*\n\n"
    for c in tracked.values():
        text += f"• {c['symbol']} ({c['name']})\n"

    bot.send_message(m.chat.id, text, reply_markup=main_menu())


# ================== ALERT ENGINE ==================

def price_watcher():
    while True:
        for pair, c in list(tracked.items()):
            try:
                data = fetch_pair(c["chain"], pair)
                if not data:
                    continue

                price = float(data["priceUsd"])
                change_1h = ((price - c["price_1h"]) / c["price_1h"]) * 100

                risk = calc_risk(data)
                anti = anti_rug(data)

                # памп / дамп
                if abs(change_1h) >= ALERT_THRESHOLD:
                    direction = "📈 ПАМП" if change_1h > 0 else "📉 ДАМП"
                    bot.send_message(
                        c["chat_id"],
                        f"{direction} *{c['symbol']}*\n\n"
                        f"Изменение 1ч: `{change_1h:.2f}%`\n"
                        f"💰 Цена: `${price}`\n"
                        f"⚠️ Риск манипуляций: `{risk}%`\n"
                        f"🧠 Anti-Rug: `{anti}/100`"
                    )
                    c["price_1h"] = price

                c["last_price"] = price

            except Exception:
                pass

        save_data()
        time.sleep(PRICE_CHECK_INTERVAL)


# ================== START ==================

threading.Thread(target=price_watcher, daemon=True).start()

bot.remove_webhook()
print("🤖 Bot polling started")
bot.infinity_polling(skip_pending=True)
