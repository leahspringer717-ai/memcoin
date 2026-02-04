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

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

BOT_TOKEN = BOT_TOKEN.strip()
DATA_FILE = "data.json"
CHECK_INTERVAL = 60  # сек
DEX_API = "https://api.dexscreener.com/latest/dex/pairs"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# ================== STORAGE ==================
lock = threading.Lock()

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_data()

def user_data(uid):
    if str(uid) not in data:
        data[str(uid)] = {"coins": {}}
    return data[str(uid)]

# ================== UI ==================
def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить монету"))
    kb.add(KeyboardButton("📊 Мои монеты"), KeyboardButton("⚙️ Настройки"))
    return kb

def coin_inline(addr, dex_url):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔗 Открыть DEX", url=dex_url))
    kb.add(
        InlineKeyboardButton("⚙️ Алерты", callback_data=f"alerts:{addr}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete:{addr}")
    )
    return kb

# ================== START ==================
@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(
        msg.chat.id,
        "🤖 *MemCoin Watcher*\n\n"
        "Я умею:\n"
        "• 📈 Алерты роста/падения за 1 час\n"
        "• 🚀 Детект пампа\n"
        "• ⚠️ Anti-rug / риск манипуляций\n"
        "• 💾 Сохранять твои монеты\n\n"
        "Добавь ссылку с DexScreener 👇",
        reply_markup=main_menu()
    )

# ================== ADD COIN ==================
@bot.message_handler(func=lambda m: m.text == "➕ Добавить монету")
def ask_link(msg):
    sent = bot.send_message(msg.chat.id, "🔗 Пришли ссылку DexScreener")
    bot.register_next_step_handler(sent, process_link)

def process_link(msg):
    try:
        url = msg.text.strip()
        pair_id = url.rstrip("/").split("/")[-1]
        r = requests.get(f"{DEX_API}/base/{pair_id}", timeout=10).json()
        pair = r["pairs"][0]

        if pair["quoteToken"]["symbol"] != "USDT":
            bot.send_message(msg.chat.id, "❌ Только пары с USDT")
            return

        u = user_data(msg.chat.id)
        u["coins"][pair["pairAddress"]] = {
            "symbol": pair["baseToken"]["symbol"],
            "name": pair["baseToken"]["name"],
            "dex": pair["dexId"],
            "url": pair["url"],
            "alert_up": 10,
            "alert_down": 10,
            "last_price": float(pair["priceUsd"]),
            "last_alert": 0,
        }

        save_data(data)

        bot.send_message(
            msg.chat.id,
            f"✅ *{pair['baseToken']['symbol']}* добавлена",
            reply_markup=coin_inline(pair["pairAddress"], pair["url"])
        )

    except Exception as e:
        bot.send_message(msg.chat.id, "❌ Не удалось добавить монету")

# ================== LIST ==================
@bot.message_handler(func=lambda m: m.text == "📊 Мои монеты")
def list_coins(msg):
    u = user_data(msg.chat.id)
    if not u["coins"]:
        bot.send_message(msg.chat.id, "Пока нет монет")
        return

    for addr, c in u["coins"].items():
        bot.send_message(
            msg.chat.id,
            f"🪙 *{c['symbol']}*\n"
            f"📈 Алерт вверх: {c['alert_up']}%\n"
            f"📉 Алерт вниз: {c['alert_down']}%",
            reply_markup=coin_inline(addr, c["url"])
        )

# ================== CALLBACKS ==================
@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):
    uid = str(call.message.chat.id)
    u = user_data(uid)

    if call.data.startswith("delete:"):
        addr = call.data.split(":")[1]
        if addr in u["coins"]:
            del u["coins"][addr]
            save_data(data)
            bot.edit_message_text("❌ Монета удалена", call.message.chat.id, call.message.message_id)

    if call.data.startswith("alerts:"):
        addr = call.data.split(":")[1]
        kb = InlineKeyboardMarkup()
        for p in [5, 10, 20]:
            kb.add(
                InlineKeyboardButton(f"📈 +{p}%", callback_data=f"up:{addr}:{p}"),
                InlineKeyboardButton(f"📉 -{p}%", callback_data=f"down:{addr}:{p}")
            )
        bot.edit_message_text("⚙️ Настрой алерты:", call.message.chat.id, call.message.message_id, reply_markup=kb)

    if call.data.startswith("up:") or call.data.startswith("down:"):
        t, addr, val = call.data.split(":")
        if addr in u["coins"]:
            if t == "up":
                u["coins"][addr]["alert_up"] = int(val)
            else:
                u["coins"][addr]["alert_down"] = int(val)
            save_data(data)
            bot.answer_callback_query(call.id, "✅ Сохранено")

# ================== ANALYTICS ==================
def risk_score(pair):
    score = 0
    if pair["liquidity"]["usd"] < 50000:
        score += 30
    if pair["volume"]["h1"] < 10000:
        score += 30
    if pair["priceChange"]["h1"] > 50:
        score += 40
    return min(score, 100)

# ================== WATCHER ==================
def watcher():
    while True:
        try:
            for uid, u in data.items():
                for addr, c in u["coins"].items():
                    r = requests.get(f"{DEX_API}/base/{addr}", timeout=10).json()
                    pair = r["pairs"][0]

                    price = float(pair["priceUsd"])
                    old = c["last_price"]
                    change = ((price - old) / old) * 100

                    now = time.time()
                    if now - c["last_alert"] < 3600:
                        continue

                    if change >= c["alert_up"] or change <= -c["alert_down"]:
                        risk = risk_score(pair)
                        bot.send_message(
                            uid,
                            f"🚨 *АЛЕРТ {c['symbol']}*\n"
                            f"Цена: ${price:.6f}\n"
                            f"Изм. 1ч: {change:.2f}%\n"
                            f"⚠️ Риск: {risk}%",
                            reply_markup=coin_inline(addr, c["url"])
                        )
                        c["last_alert"] = now
                        c["last_price"] = price
                        save_data(data)
        except:
            pass

        time.sleep(CHECK_INTERVAL)

# ================== RUN ==================
threading.Thread(target=watcher, daemon=True).start()
bot.infinity_polling(skip_pending=True)
