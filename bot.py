import os
import re
import json
import time
import threading
import requests
import telebot
from telebot import types

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

DATA_FILE = "coins.json"
CHECK_INTERVAL = 300
DEX_API = "https://api.dexscreener.com/latest/dex/pairs"

# ================= STORAGE =================

if os.path.exists(DATA_FILE):
    with open(DATA_FILE) as f:
        USERS = json.load(f)
else:
    USERS = {}


def save():
    with open(DATA_FILE, "w") as f:
        json.dump(USERS, f, indent=2)


def user(uid):
    return USERS.setdefault(str(uid), {})

# ================= HELPERS =================

def extract(url):
    m = re.search(r"dexscreener\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9x]+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def fetch(chain, pair):
    try:
        r = requests.get(f"{DEX_API}/{chain}/{pair}", timeout=10).json()
        for p in r.get("pairs", []):
            if p.get("quoteToken", {}).get("symbol") == "USDT":
                return p
    except:
        pass
    return None


def pump_score(change, vol, liq):
    score = 0
    score += min(abs(change) * 2, 40)
    score += min(vol / 50000, 30)
    score += min(liq / 100000, 30)
    return int(min(score, 100))


def anti_rug(liq, vol, fdv):
    risk = 100
    if liq > 300_000: risk -= 30
    if vol > 200_000: risk -= 30
    if fdv and liq / fdv > 0.05: risk -= 20
    return max(1, min(100, risk))


# ================= UI =================

def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Add coin", callback_data="add"))
    kb.add(types.InlineKeyboardButton("📊 My coins", callback_data="list"))
    return kb


def coin_kb(chain, pair):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📈 Chart", url=f"https://dexscreener.com/{chain}/{pair}"))
    kb.add(types.InlineKeyboardButton("🗑 Remove", callback_data=f"del:{pair}"))
    return kb


# ================= BOT =================

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *MemCoin Scanner PRO*\n\n"
        "• 📊 Pump Score 0–100\n"
        "• 🧠 Anti-Rug analysis\n"
        "• 🚨 Pump / Dump alerts (1h)\n"
        "• 👥 Multi-user support\n"
        "• 📈 Charts\n\n"
        "Нажми кнопку ниже 👇",
        reply_markup=main_menu()
    )


@bot.callback_query_handler(func=lambda c: c.data == "add")
def add_prompt(c):
    bot.send_message(c.message.chat.id, "🔗 Send DexScreener link (USDT only)")


@bot.message_handler(func=lambda m: m.text and "dexscreener.com" in m.text)
def add_coin(m):
    chain, pair = extract(m.text)
    data = fetch(chain, pair)
    if not data:
        bot.reply_to(m, "❌ Failed to load pair")
        return

    u = user(m.from_user.id)

    price = float(data["priceUsd"])
    u[pair] = {
        "chain": chain,
        "symbol": data["baseToken"]["symbol"],
        "price": price,
        "last_price": price,
        "alerted": False,
        "last_check": time.time()
    }
    save()

    bot.send_message(
        m.chat.id,
        f"✅ *{u[pair]['symbol']}* added\nPrice: ${price}",
        reply_markup=coin_kb(chain, pair)
    )


@bot.callback_query_handler(func=lambda c: c.data == "list")
def list_coins(c):
    u = user(c.from_user.id)
    if not u:
        bot.send_message(c.message.chat.id, "📭 No coins")
        return

    for pair, coin in u.items():
        bot.send_message(
            c.message.chat.id,
            f"*{coin['symbol']}*\nPrice: ${coin['price']}",
            reply_markup=coin_kb(coin["chain"], pair)
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("del:"))
def delete(c):
    pair = c.data.split(":")[1]
    u = user(c.from_user.id)
    u.pop(pair, None)
    save()
    bot.edit_message_text("🗑 Removed", c.message.chat.id, c.message.message_id)


# ================= WATCHER =================

def watcher():
    while True:
        for uid, coins in USERS.items():
            for pair, c in coins.items():
                data = fetch(c["chain"], pair)
                if not data:
                    continue

                price = float(data["priceUsd"])
                vol = data.get("volume", {}).get("h1", 0)
                liq = data.get("liquidity", {}).get("usd", 0)
                fdv = data.get("fdv", 0)

                change = ((price - c["last_price"]) / c["last_price"]) * 100
                score = pump_score(change, vol, liq)
                risk = anti_rug(liq, vol, fdv)

                if abs(change) >= 10 and not c["alerted"]:
                    bot.send_message(
                        uid,
                        f"{'📈 PUMP' if change > 0 else '📉 DUMP'} *{c['symbol']}*\n\n"
                        f"Change: {change:.2f}% (1h)\n"
                        f"💰 Liquidity: ${liq:,.0f}\n"
                        f"📊 Volume 1h: ${vol:,.0f}\n\n"
                        f"📊 Pump Score: *{score}/100*\n"
                        f"⚠️ Anti-Rug Risk: *{risk}/100*"
                    )
                    c["alerted"] = True

                c["last_price"] = price
                save()

        time.sleep(CHECK_INTERVAL)


# ================= START =================

threading.Thread(target=watcher, daemon=True).start()
bot.remove_webhook()
print("🤖 Bot started")
bot.infinity_polling(skip_pending=True)
