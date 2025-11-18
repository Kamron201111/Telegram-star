import logging
import json
import os
import random
from datetime import datetime
from enum import Enum
from typing import Dict
from flask import Flask, request

import telebot
from telebot.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup
)
import redis

# ------------------ CONFIG ------------------
TOKEN = "7810689974:AAHpifjmAG_tOwDvIGRNG4L1ah8mix38cWU"
ADMIN_CHAT_ID = "6498632307"
SUPPORT_USERNAME = "@kamron201"

# Render'da Redis bo'lmasa ham ishlashi uchun fallback
REDIS_URL = os.getenv("REDIS_URL", None)

WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TOKEN}"

# ------------------ LOGGING ------------------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------ TELEBOT ------------------
bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# ------------------ REDIS OR MEMORY ------------------
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
except:
    redis_client = None

user_states = {}  # fallback in memory

# ------------------ CONSTANTS ------------------
class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class UserRole(Enum):
    USER = "user"
    ADMIN = "admin"

TELEGRAM_STARS_PACKAGES = {
    "buy_50": {"amount": 50, "price": 80, "points": 1, "discount": 0},
    "buy_75": {"amount": 75, "price": 130, "points": 2, "discount": 5},
    "buy_100": {"amount": 100, "price": 160, "points": 2, "discount": 10},
    "buy_250": {"amount": 250, "price": 380, "points": 4, "discount": 15},
    "buy_500": {"amount": 500, "price": 780, "points": 8, "discount": 20},
    "buy_750": {"amount": 750, "price": 1300, "points": 12, "discount": 25},
    "buy_1000": {"amount": 1000, "price": 1580, "points": 15, "discount": 30},
}

# ------------------ SECURITY ------------------
class SecurityManager:
    @staticmethod
    def validate_user_input(text: str, max_length: int = 100) -> bool:
        if not text or len(text) > max_length:
            return False
        bad = ['<script>', '../', ';', '--']
        return not any(b in text.lower() for b in bad)

    @staticmethod
    def generate_order_id():
        return f"ORD{int(datetime.now().timestamp())}{random.randint(1000, 9999)}"

# ------------------ DATABASE ------------------
class DatabaseManager:
    def get_user_data(self, user_id: int) -> Dict:
        if not redis_client:
            return self._default_user()

        key = f"user:{user_id}"
        data = redis_client.get(key)
        if data:
            return json.loads(data)

        default = self._default_user()
        self.update_user_data(user_id, default)
        return default

    def _default_user(self):
        return {
            "username": "",
            "total_stars": 0,
            "total_spent": 0,
            "points": 0,
            "orders_count": 0,
            "role": UserRole.USER.value,
            "registration_date": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat()
        }

    def update_user_data(self, user_id: int, data: Dict):
        if not redis_client:
            return
        key = f"user:{user_id}"
        current = self.get_user_data(user_id)
        current.update(data)
        redis_client.set(key, json.dumps(current), ex=86400 * 30)

    def create_order(self, order_data: Dict):
        order_id = SecurityManager.generate_order_id()
        order_data["order_id"] = order_id
        order_data["created_at"] = datetime.now().isoformat()

        if redis_client:
            redis_client.set(f"order:{order_id}", json.dumps(order_data), ex=86400 * 7)

        return order_id

db = DatabaseManager()

def get_user_role(user_id):
    return UserRole.ADMIN if str(user_id) == ADMIN_CHAT_ID else UserRole.USER

# ------------------ BOT HANDLERS ------------------

@bot.message_handler(commands=['start'])
def start_handler(message):
    user_id = message.from_user.id
    db.update_user_data(user_id, {
        "username": message.from_user.username or "",
        "first_name": message.from_user.first_name or ""
    })

    if get_user_role(user_id) == UserRole.ADMIN:
        keyboard = [
            [KeyboardButton("📊 Statistika"), KeyboardButton("📦 Buyurtmalar")],
            [KeyboardButton("👥 Foydalanuvchilar")]
        ]
    else:
        keyboard = [
            [KeyboardButton("🛒 Stars sotib olish"), KeyboardButton("👤 Profil")],
            [KeyboardButton("🆘 Yordam")]
        ]

    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    text = (
        f"🌟 Xush kelibsiz, {message.from_user.first_name}!\n\n"
        "⚡ <b>Telegram Stars bot</b> — starsni tez va ishonchli xarid qiling.\n\n"
        "Quyidan amalni tanlang 👇"
    )

    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='HTML')


@bot.message_handler(func=lambda m: m.text == "🛒 Stars sotib olish")
def show_stars_packages(message):
    keyboard = []
    for key, p in TELEGRAM_STARS_PACKAGES.items():
        disc = f" 🔥 -{p['discount']}%" if p['discount'] > 0 else ""
        keyboard.append([InlineKeyboardButton(
            f"{p['amount']} Stars - {p['price']} so'm{disc}",
            callback_data=key
        )])

    bot.send_message(
        message.chat.id,
        "🎯 <b>Paketni tanlang:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_package_selection(call):
    package = TELEGRAM_STARS_PACKAGES.get(call.data)
    if not package:
        bot.answer_callback_query(call.id, "Xato!")
        return

    user_states[call.from_user.id] = {
        "current_order": package,
        "step": "waiting_username"
    }

    text = (
        f"⭐ <b>{package['amount']} Stars</b>\n"
        f"💰 Narx: {package['price']} so'm\n"
        f"🎁 Bonus: {package['points']}\n\n"
        "📝 Telegram usernamingizni yuboring (@siz)"
    )

    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode='HTML')


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("step") == "waiting_username")
def handle_username(message):
    username = message.text.strip().replace("@", "")
    if not SecurityManager.validate_user_input(username):
        bot.send_message(message.chat.id, "❌ Username noto‘g‘ri. Qayta kiriting:")
        return

    user_states[message.from_user.id]["telegram_username"] = username
    user_states[message.from_user.id]["step"] = "waiting_payment"

    bot.send_message(
        message.chat.id,
        (
            f"💳 <b>To‘lov uchun karta:</b>\n"
            f"<code>2202 2002 2020 2020</code>\n\n"
            f"📸 Chek rasmini yuboring"
        ),
        parse_mode='HTML'
    )


@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.from_user.id, {}).get("step") == "waiting_payment")
def payment_received(message):
    state = user_states.pop(message.from_user.id)
    order = state["current_order"]
    username = state["telegram_username"]

    order_data = {
        "user_id": message.from_user.id,
        "telegram_username": username,
        "stars_amount": order["amount"],
        "price": order["price"],
        "points": order["points"],
    }

    order_id = db.create_order(order_data)

    bot.send_message(
        message.chat.id,
        f"📦 Buyurtma qabul qilindi!\n🆔 Buyurtma: #{order_id}\n⏱ Tekshirilmoqda…",
        parse_mode='HTML'
    )


@bot.message_handler(func=lambda m: m.text == "👤 Profil")
def profile_handler(message):
    data = db.get_user_data(message.from_user.id)

    text = (
        "👤 <b>Profil</b>\n\n"
        f"⭐ Sotib olingan stars: {data['total_stars']}\n"
        f"💰 Sarflangan: {data['total_spent']} so‘m\n"
        f"🎁 Ballar: {data['points']}"
    )

    bot.send_message(message.chat.id, text, parse_mode='HTML')


@bot.message_handler(func=lambda m: m.text == "🆘 Yordam")
def help_handler(message):
    bot.send_message(
        message.chat.id,
        f"🆘 Yordam uchun: {SUPPORT_USERNAME}",
        parse_mode='HTML'
    )

# ------------------ WEBHOOK SERVER ------------------

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook_handler():
    update = telebot.types.Update.de_json(request.data.decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/", methods=["GET"])
def index():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    return "Webhook set!", 200


# ------------------ RUN APP ------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
# ===================== ADMIN PANEL BLOKI ==========================

# 🔧 ADMIN PANEL GA KIRISH
@bot.message_handler(func=lambda m: m.text == "📊 Statistika" and str(m.from_user.id) == ADMIN_CHAT_ID)
def admin_stats(message):
    total_users = 0
    total_orders = 0
    total_stars_sold = 0

    if redis_client:
        for key in redis_client.scan_iter("user:*"):
            total_users += 1
        for key in redis_client.scan_iter("order:*"):
            total_orders += 1
            order = json.loads(redis_client.get(key))
            total_stars_sold += order.get("stars_amount", 0)

    text = (
        "📊 <b>ADMIN STATISTIKA</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{total_users}</b>\n"
        f"📦 Buyurtmalar: <b>{total_orders}</b>\n"
        f"⭐ Sotilgan Stars: <b>{total_stars_sold}</b>\n\n"
        "⚙ Quyidagidan birini tanlang:"
    )

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📦 Buyurtmalar", callback_data="admin_orders"),
        InlineKeyboardButton("👥 Userlar", callback_data="admin_users")
    )
    markup.add(
        InlineKeyboardButton("💳 Karta sozlamalari", callback_data="admin_cards")
    )

    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")


# 📦 BUYURTMALAR BLOKI
@bot.callback_query_handler(func=lambda c: c.data == "admin_orders")
def admin_orders(call):
    text = "<b>📦 Buyurtmalar ro‘yxati</b>\n\n"

    if not redis_client:
        bot.send_message(call.message.chat.id, "❗ Redis yo‘q, buyurtmalarni olish imkonsiz")
        return

    orders = list(redis_client.scan_iter("order:*"))
    if not orders:
        bot.send_message(call.message.chat.id, "📭 Buyurtmalar yo‘q")
        return

    for key in orders[:20]:  # 20 ta buyurtma limit
        order = json.loads(redis_client.get(key))
        text += (
            f"🆔 <b>{order['order_id']}</b>\n"
            f"👤 @{order['telegram_username']}\n"
            f"⭐ {order['stars_amount']} Stars\n"
            f"💰 {order['price']} so‘m\n"
            f"🎁 +{order['points']} ball\n"
            f"⏰ {order['created_at']}\n"
            "----------------------\n"
        )

    bot.send_message(call.message.chat.id, text, parse_mode="HTML")


# 👥 USERLAR BLOKI
@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def admin_users(call):
    text = "<b>👥 Foydalanuvchilar</b>\n\n"
    count = 0

    if redis_client:
        for key in redis_client.scan_iter("user:*"):
            user = json.loads(redis_client.get(key))
            username = user.get("username", "no username")
            text += f"👤 @{username} | ⭐ {user['total_stars']} Stars | 💰 {user['total_spent']} so‘m\n"
            count += 1
            if count >= 30:
                break

    bot.send_message(call.message.chat.id, text or "Foydalanuvchi yo‘q", parse_mode="HTML")


# 💳 KARTA SOZLAMALARI MENYUSI
@bot.callback_query_handler(func=lambda c: c.data == "admin_cards")
def admin_cards(call):
    current_card = redis_client.get("payment_card") if redis_client else "2202 2002 2020 2020"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("➕ Karta qo‘shish", callback_data="add_card"),
        InlineKeyboardButton("♻️ Kartani almashtirish", callback_data="change_card")
    )
    markup.add(InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back"))

    bot.send_message(
        call.message.chat.id,
        f"💳 <b>Karta sozlamalari</b>\n\n"
        f"🔐 Joriy karta:\n<code>{current_card}</code>",
        parse_mode="HTML",
        reply_markup=markup
    )


# ➕ KARTA QO‘SHISH
@bot.callback_query_handler(func=lambda c: c.data == "add_card")
def add_card(call):
    user_states[call.from_user.id] = {"step": "add_new_card"}

    bot.send_message(call.message.chat.id, "💳 Yangi kartani kiriting (faqat raqam):")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("step") == "add_new_card")
def save_new_card(message):
    card = message.text.strip().replace(" ", "")

    if not card.isdigit() or len(card) not in [16]:
        bot.send_message(message.chat.id, "❌ Karta raqami noto‘g‘ri. Qayta kiriting.")
        return

    if redis_client:
        redis_client.set("payment_card", card)

    user_states.pop(message.from_user.id, None)

    bot.send_message(message.chat.id, f"✅ Yangi karta qo‘shildi:\n<code>{card}</code>", parse_mode="HTML")


# ♻️ KARTANI ALMASHTIRISH
@bot.callback_query_handler(func=lambda c: c.data == "change_card")
def change_card(call):
    user_states[call.from_user.id] = {"step": "change_card"}

    bot.send_message(call.message.chat.id, "♻️ Yangi karta raqamini kiriting:")


@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get("step") == "change_card")
def update_card(message):
    card = message.text.strip().replace(" ", "")

    if not card.isdigit() or len(card) != 16:
        bot.send_message(message.chat.id, "❌ Karta formati noto‘g‘ri. Qayta kiriting.")
        return

    if redis_client:
        redis_client.set("payment_card", card)

    user_states.pop(message.from_user.id, None)

    bot.send_message(message.chat.id, f"♻️ Karta yangilandi:\n<code>{card}</code>", parse_mode="HTML")


# 🔙 ADMIN PANEL ORQAGA
@bot.callback_query_handler(func=lambda c: c.data == "admin_back")
def admin_back(call):
    bot.send_message(call.message.chat.id, "🔙 Admin panelga qayting: 📊 Statistika tugmasi orqali")
