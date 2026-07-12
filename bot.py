"""
bot.py
Telegram Credit Link-Exchange Bot

Flow:
  /start -> main menu with 3 buttons:
      1) Account Info      -> username / user id / credit balance / total clicks
      2) Add Daraz Link     -> costs 1 credit, posts a link for exactly ONE other
                               user to visit & claim credit for
      3) Earn Credit        -> shows one link (not your own, not already claimed
                               by you). Click "Visit", then "I've Visited -
                               Claim Credit" to get +1 credit. That link is then
                               marked done (only ONE person can claim each link).

Run:
    pip install -r requirements.txt
    export BOT_TOKEN="123456:ABC-your-telegram-bot-token"
    python bot.py
"""

import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
REFERRAL_BONUS = 5

URL_REGEX = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)

# conversation "state" flags kept in context.user_data
WAITING_FOR_LINK = "waiting_for_link"
WAITING_FOR_CREDIT_AMOUNT = "waiting_for_credit_amount"
PENDING_LINK_URL = "pending_link_url"
PENDING_LINK_ID = "pending_link_id"


# ------------------------------------------------------------- keyboards --

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👤 Account Info", callback_data="menu_account")],
            [InlineKeyboardButton("➕ Add Daraz Link", callback_data="menu_add_link")],
            [InlineKeyboardButton("💰 Earn Credit", callback_data="menu_earn_credit")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_home")]]
    )


# --------------------------------------------------------------- helpers --

async def send_main_menu(update_or_query, text: str = "একটা অপশন বেছে নিন 👇"):
    keyboard = main_menu_keyboard()
    if hasattr(update_or_query, "message") and update_or_query.message:
        # it's a CallbackQuery
        await update_or_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update_or_query.reply_text(text, reply_markup=keyboard)


# ------------------------------------------------------------- handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new_user = not db.user_exists(user.id)
    db.ensure_user(user.id, user.username or user.first_name)

    if is_new_user and context.args:
        await process_referral(update, context, user.id)

    context.user_data.pop(WAITING_FOR_LINK, None)
    context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
    context.user_data.pop(PENDING_LINK_URL, None)
    context.user_data.pop(PENDING_LINK_ID, None)
    await update.message.reply_text(
        "স্বাগতম! এই বট দিয়ে আপনি Daraz লিংক পোস্ট করতে পারবেন এবং অন্যের "
        "লিংকে ক্লিক করে ক্রেডিট আয় করতে পারবেন।\n\n"
        "নিয়ম:\n"
        "• লিংক পোস্ট করতে যত জনের কাছে পাঠাতে চান, তত ক্রেডিট লাগবে (যেমন ৫ ক্রেডিট = ৫ জনের কাছে যাবে)।\n"
        "• প্রতিটা লিংক প্রতিটা মানুষের কাছে শুধু ১ বার আসবে, একবার দেখা লিংক আর দেখাবে না।\n"
        "• যত জনকে বলেছিলেন ততজন ক্লিক করে ফেললে লিংকটা সবার ফিড থেকে চলে যাবে।\n"
        "• কেউ ক্লিক করে ভিজিট কনফার্ম করলে সে +১ ক্রেডিট পাবে।\n"
        f"• কাউকে রেফার করে জয়েন করালে আপনি পাবেন +{REFERRAL_BONUS} ক্রেডিট (Account Info এ আপনার রেফার লিংক পাবেন)।\n\n"
        "একটা অপশন বেছে নিন 👇",
        reply_markup=main_menu_keyboard(),
    )


async def process_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, new_user_id: int):
    """Parses /start ref<id> deep-links and credits the referrer, once, for new users only."""
    arg = context.args[0]
    if not arg.startswith("ref"):
        return

    try:
        referrer_id = int(arg[3:])
    except ValueError:
        return

    if referrer_id == new_user_id:
        return  # can't refer yourself

    if not db.user_exists(referrer_id):
        return  # unknown referrer id, ignore

    db.set_referral(new_user_id, referrer_id, bonus=REFERRAL_BONUS)

    try:
        await context.bot.send_message(
            referrer_id,
            f"🎉 আপনার রেফার লিংক দিয়ে একজন নতুন ইউজার জয়েন করেছে!\n"
            f"আপনি পেয়েছেন +{REFERRAL_BONUS} ক্রেডিট।",
        )
    except Exception:
        pass  # referrer may have blocked the bot; ignore silently


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    db.ensure_user(user.id, user.username or user.first_name)

    data = query.data

    if data == "menu_home":
        context.user_data.pop(WAITING_FOR_LINK, None)
        context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
        context.user_data.pop(PENDING_LINK_URL, None)
        context.user_data.pop(PENDING_LINK_ID, None)
        await send_main_menu(query)

    elif data == "menu_account":
        await show_account_info(query, context, user.id)

    elif data == "menu_add_link":
        await start_add_link(query, context, user.id)

    elif data == "menu_earn_credit":
        await show_earn_credit(query, context, user.id)

    elif data == "claim_credit":
        await claim_credit(query, context, user.id)


async def show_account_info(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    u = db.get_user(user_id)
    bot_username = context.bot.username
    ref_link = f"https://t.me/{bot_username}?start=ref{user_id}"
    text = (
        "👤 <b>Account Info</b>\n\n"
        f"Username: @{u['username']}\n"
        f"User ID: <code>{u['user_id']}</code>\n"
        f"Credit Balance: <b>{u['credit_balance']}</b>\n"
        f"Total Clicks (earned): <b>{u['total_clicks']}</b>\n"
        f"Total Referrals: <b>{u['total_referrals']}</b>\n\n"
        f"🔗 <b>আপনার রেফার লিংক:</b>\n<code>{ref_link}</code>\n\n"
        f"এই লিংক দিয়ে কেউ প্রথমবার বট এ জয়েন করলে আপনি পাবেন +{REFERRAL_BONUS} ক্রেডিট।"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_keyboard()
    )


async def start_add_link(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    u = db.get_user(user_id)
    if u["credit_balance"] < 1:
        await query.edit_message_text(
            "❌ আপনার কাছে যথেষ্ট ক্রেডিট নেই।\n"
            "লিংক পোস্ট করতে কমপক্ষে ১ ক্রেডিট লাগবে।\n"
            "আগে \"Earn Credit\" থেকে ক্রেডিট আয় করুন।",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    context.user_data[WAITING_FOR_LINK] = True
    await query.edit_message_text(
        f"🔗 আপনার Daraz লিংকটা এখানে পাঠান (শুধু লিংকটা মেসেজ হিসেবে পাঠান)।\n\n"
        f"আপনার বর্তমান ব্যালেন্স: {u['credit_balance']} ক্রেডিট।\n"
        "লিংক পাঠানোর পর জিজ্ঞেস করব কত ক্রেডিট খরচ করতে চান — "
        "যত ক্রেডিট খরচ করবেন, ততজন ইউনিক মানুষের কাছে এই লিংক দেখানো হবে।",
        reply_markup=back_to_menu_keyboard(),
    )


async def receive_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Router for plain text messages during the 'add link' flow."""
    if context.user_data.get(WAITING_FOR_LINK):
        await receive_link_url(update, context)
        return
    if context.user_data.get(WAITING_FOR_CREDIT_AMOUNT):
        await receive_credit_amount(update, context)
        return
    # not in any flow, ignore silently


async def receive_link_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()

    if not URL_REGEX.match(text):
        await update.message.reply_text(
            "⚠️ এটা একটা ভ্যালিড লিংক মনে হচ্ছে না। লিংকটা http:// অথবা https:// দিয়ে শুরু "
            "হতে হবে। আবার চেষ্টা করুন, অথবা /start চাপুন মেনুতে ফিরে যেতে।"
        )
        return

    u = db.get_user(user.id)
    if u["credit_balance"] < 1:
        context.user_data.pop(WAITING_FOR_LINK, None)
        await update.message.reply_text(
            "❌ দুঃখিত, আপনার ক্রেডিট শেষ হয়ে গেছে। আগে ক্রেডিট আয় করুন।",
            reply_markup=main_menu_keyboard(),
        )
        return

    context.user_data[PENDING_LINK_URL] = text
    context.user_data.pop(WAITING_FOR_LINK, None)
    context.user_data[WAITING_FOR_CREDIT_AMOUNT] = True

    await update.message.reply_text(
        f"👍 লিংক পাওয়া গেছে।\n"
        f"আপনার ব্যালেন্স: {u['credit_balance']} ক্রেডিট।\n\n"
        f"কত ক্রেডিট খরচ করে পোস্ট করতে চান? (১ থেকে {u['credit_balance']} এর মধ্যে একটা সংখ্যা লিখুন)\n"
        "উদাহরণ: 5 লিখলে এই লিংক ঠিক ৫ জন ইউনিক মানুষের কাছে দেখানো হবে, "
        "৫ জন ক্লিক করে ফেললে লিংকটা আর কারো কাছে দেখা যাবে না।"
    )


async def receive_credit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    url = context.user_data.get(PENDING_LINK_URL)

    if not url:
        # safety fallback, shouldn't normally happen
        context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
        await update.message.reply_text(
            "⚠️ কিছু একটা সমস্যা হয়েছে, আবার /start চাপুন।", reply_markup=main_menu_keyboard()
        )
        return

    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("⚠️ দয়া করে একটা সঠিক সংখ্যা লিখুন (যেমন: 1, 3, 5)।")
        return

    amount = int(text)
    u = db.get_user(user.id)

    if amount > u["credit_balance"]:
        await update.message.reply_text(
            f"⚠️ আপনার কাছে শুধু {u['credit_balance']} ক্রেডিট আছে। এর বেশি সংখ্যা দেওয়া যাবে না। "
            "আবার একটা বৈধ সংখ্যা লিখুন।"
        )
        return

    db.deduct_credit(user.id, amount)
    link_id = db.create_link(user.id, url, needed_clicks=amount)

    context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
    context.user_data.pop(PENDING_LINK_URL, None)

    await update.message.reply_text(
        f"✅ আপনার লিংক পোস্ট করা হয়েছে! {amount} ক্রেডিট কাটা হয়েছে।\n"
        f"এই লিংক এখন ঠিক {amount} জন ইউনিক মানুষের কাছে দেখানো হবে। "
        f"সবাই ক্লিক করে ফেললে লিংকটা নিজে থেকেই সরে যাবে।",
        reply_markup=main_menu_keyboard(),
    )


async def show_earn_credit(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    link = db.get_next_link_for_user(user_id)
    if not link:
        await query.edit_message_text(
            "😔 এই মুহূর্তে ক্লিক করার মতো কোনো নতুন লিংক নেই। কিছুক্ষণ পর আবার চেষ্টা করুন।",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    context.user_data[PENDING_LINK_ID] = link["id"]

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 লিংক ভিজিট করুন", url=link["url"])],
            [InlineKeyboardButton("✅ ভিজিট করেছি, ক্রেডিট নিন", callback_data="claim_credit")],
            [InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu_home")],
        ]
    )
    await query.edit_message_text(
        "💰 <b>Earn Credit</b>\n\n"
        "নিচের লিংকে ক্লিক করে ভিজিট করুন, তারপর \"ভিজিট করেছি\" বাটনে চাপুন +১ ক্রেডিট পেতে।",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def claim_credit(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    link_id = context.user_data.get(PENDING_LINK_ID)
    if not link_id:
        await query.edit_message_text(
            "⚠️ কোনো পেন্ডিং লিংক পাওয়া যায়নি। আবার \"Earn Credit\" থেকে চেষ্টা করুন.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    link = db.get_link(link_id)
    if not link or link["status"] != "active":
        await query.edit_message_text(
            "⚠️ এই লিংকটা আর available নেই (হয়তো অন্য কেউ আগেই ক্লেইম করেছে)।",
            reply_markup=back_to_menu_keyboard(),
        )
        context.user_data.pop(PENDING_LINK_ID, None)
        return

    inserted = db.record_click(link_id, user_id)
    if not inserted:
        await query.edit_message_text(
            "⚠️ আপনি ইতিমধ্যে এই লিংকের জন্য ক্রেডিট নিয়েছেন।",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # bump this link's click count; it auto-completes once needed_clicks is reached
    db.increment_link_click_and_maybe_complete(link_id)
    db.add_credit(user_id, 1)
    db.increment_total_clicks(user_id)
    context.user_data.pop(PENDING_LINK_ID, None)

    u = db.get_user(user_id)
    await query.edit_message_text(
        f"🎉 অভিনন্দন! আপনি +১ ক্রেডিট পেয়েছেন।\n"
        f"বর্তমান ব্যালেন্স: <b>{u['credit_balance']}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_keyboard(),
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "বুঝতে পারিনি। /start চাপুন মেনু দেখতে।"
    )


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit(
            "BOT_TOKEN সেট করা হয়নি। export BOT_TOKEN='your:token' চালিয়ে আবার রান করুন।"
        )

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link_message))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
