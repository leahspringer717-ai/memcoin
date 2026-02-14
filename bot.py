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
ENGINE_SIGNAL_THRESHOLD = 75
ENGINE_RESET_THRESHOLD = 60
MAX_HISTORY = 30
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
            "volume": float(p.get("volume", {}).get("h24", 0)),
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

# ================== ENGINE ==================
def calculate_engine(coin, mexc_price):
    history = coin["history"]
    if len(history) < 6:
        return 0

    price_now = history[-1]["price"]
    price_30m_ago = history[0]["price"]
    price_5m_ago = history[-6]["price"]

    vol_now = history[-1]["volume"]
    vol_old = history[0]["volume"]

    # 30m Trend (40)
    trend = (price_now - price_30m_ago) / price_30m_ago * 100
    if trend > 10:
        trend_score = 40
    elif trend > 5:
        trend_score = 30
    elif trend > 2:
        trend_score = 20
    elif trend > 0:
        trend_score = 10
    else:
        trend_score = 0

    # 5m acceleration (20)
    accel = (price_now - price_5m_ago) / price_5m_ago * 100
    accel_score = min(max(accel * 4, 0), 20)

    # Volume expansion (20)
    if vol_old > 0:
        vol_change = (vol_now - vol_old) / vol_old * 100
    else:
        vol_change = 0

    if trend > 0 and vol_change > 10:
        vol_score = 20
    elif trend > 0 and vol_change > 0:
        vol_score = 15
    elif trend < 0 and vol_change > 10:
        vol_score = 5
    else:
        vol_score = 0

    # Futures bias (20)
    if mexc_price:
        spread = (mexc_price - price_now) / price_now * 100
        if spread > 2:
            fut_score = 20
        elif spread > 1:
            fut_score = 15
        elif spread > 0:
            fut_score = 10
        else:
            fut_score = 0
    else:
        fut_score = 0

    total = trend_score + accel_score + vol_score + fut_score
    return round(total, 2)

# ================== START ==================
@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(
        m.chat.id,
        "🚀 *DEX MEME ENGINE BOT*\n\n"
        "Hybrid Market Engine + Auto Signals\n\n"
        "Добавь ссылку Dexscreener (USDT)",
        reply_markup=main_menu()
    )

# ================== TEXT ==================
@bot.message_handler(func=lambda m: True)
def text_handler(m):
    u = get_user(m.chat.id)

    if m.text == "➕ Добавить монету":
        bot.send_message(m.chat.id, "Пришли ссылку Dexscreener", reply_markup=main_menu())
        return

    if m.text == "📂 Мои монеты":
        if not u["coins"]:
            bot.send_message(m.chat.id, "Монет нет", reply_markup=main_menu())
            return

        for pair, coin in u["coins"].items():
            bot.send_message(
                m.chat.id,
                f"*{coin['symbol']}*\n"
                f"Цена %: {coin['alert']}%\n"
                f"MEXC %: {coin['mexc_alert'] or '-'}%\n"
                f"Engine Score: {coin.get('last_score',0)}",
                reply_markup=coin_keyboard(pair, coin)
            )
        return

    parsed = parse_dex_link(m.text)
    if not parsed:
        return

    chain, pair = parsed
    data = get_dex_data(chain, pair)
    if not data:
        bot.send_message(m.chat.id, "Нужна USDT пара")
        return

    u["coins"][pair] = {
        "symbol": data["symbol"],
        "chain": chain,
        "last_price": data["price"],
        "alert": 10,
        "mexc_alert": None,
        "dex_url": data["url"],
        "history": [],
        "engine_triggered": False,
        "last_score": 0
    }
    save_db()

    bot.send_message(
        m.chat.id,
        f"✅ {data['symbol']} добавлена",
        reply_markup=coin_keyboard(pair, u["coins"][pair])
    )

# ================== WATCHER ==================
def watcher():
    while True:
        for uid, u in DB.items():
            for pair, coin in u["coins"].items():
                data = get_dex_data(coin["chain"], pair)
                if not data:
                    continue

                price = data["price"]
                volume = data["volume"]
                mexc_price = get_mexc_price(coin["symbol"])

                coin["history"].append({
                    "price": price,
                    "volume": volume,
                    "ts": int(time.time())
                })

                if len(coin["history"]) > MAX_HISTORY:
                    coin["history"].pop(0)

                score = calculate_engine(coin, mexc_price)
                coin["last_score"] = score

                if score >= ENGINE_SIGNAL_THRESHOLD and not coin["engine_triggered"]:
                    bot.send_message(
                        uid,
                        f"🚀 *STRONG MOMENTUM — {coin['symbol']}*\n"
                        f"Engine Score: {score}/100\n"
                        f"Цена: ${price}"
                    )
                    coin["engine_triggered"] = True

                if score < ENGINE_RESET_THRESHOLD:
                    coin["engine_triggered"] = False

                coin["last_price"] = price

                save_db()

        time.sleep(CHECK_INTERVAL)

# ================== START ==================
threading.Thread(target=watcher, daemon=True).start()
bot.remove_webhook()
bot.infinity_polling(skip_pending=True)
