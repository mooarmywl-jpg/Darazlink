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

import asyncio
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

REFERRAL_SIGNUP_BONUS = 2      # credit given to referrer the moment their referral joins
REFERRAL_TASK_COMMISSION = 0.1  # credit given to referrer every time their referral completes a task
EARN_CREDIT_REWARD = 0.8        # credit given to a user for completing "Earn Credit" (link click)
NOTIFICATION_AUTO_DELETE_SECONDS = 60  # referral notifications self-delete after this long

ADMIN_USER_IDS = {
    int(x) for x in os.environ.get("ADMIN_USER_ID", "7750119638").split(",") if x.strip()
}


def format_credit(value) -> str:
    """Show whole numbers as-is (e.g. '2'), fractional as 1 decimal (e.g. '0.8')."""
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


async def auto_delete_message(bot, chat_id: int, message_id: int, delay: int = NOTIFICATION_AUTO_DELETE_SECONDS):
    """Deletes a message after `delay` seconds. Fails silently if already gone."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def send_self_deleting_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str):
    """Sends a notification and schedules it to auto-delete shortly after."""
    try:
        msg = await context.bot.send_message(chat_id, text)
        asyncio.create_task(auto_delete_message(context.bot, chat_id, msg.message_id))
    except Exception:
        pass  # user may have blocked the bot; ignore silently

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
            [InlineKeyboardButton("ðŸ‘¤ Account Info", callback_data="menu_account")],
            [InlineKeyboardButton("âž• Add Daraz Link", callback_data="menu_add_link")],
            [InlineKeyboardButton("ðŸ’° Earn Credit", callback_data="menu_earn_credit")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("â¬…ï¸ Back to Menu", callback_data="menu_home")]]
    )


# --------------------------------------------------------------- helpers --

async def send_main_menu(update_or_query, text: str = "à¦à¦•à¦Ÿà¦¾ à¦…à¦ªà¦¶à¦¨ à¦¬à§‡à¦›à§‡ à¦¨à¦¿à¦¨ ðŸ‘‡"):
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
        "à¦¸à§à¦¬à¦¾à¦—à¦¤à¦®! à¦à¦‡ à¦¬à¦Ÿ à¦¦à¦¿à¦¯à¦¼à§‡ à¦†à¦ªà¦¨à¦¿ Daraz à¦²à¦¿à¦‚à¦• à¦ªà§‹à¦¸à§à¦Ÿ à¦•à¦°à¦¤à§‡ à¦ªà¦¾à¦°à¦¬à§‡à¦¨ à¦à¦¬à¦‚ à¦…à¦¨à§à¦¯à§‡à¦° "
        "à¦²à¦¿à¦‚à¦•à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦†à¦¯à¦¼ à¦•à¦°à¦¤à§‡ à¦ªà¦¾à¦°à¦¬à§‡à¦¨à¥¤\n\n"
        "à¦¨à¦¿à¦¯à¦¼à¦®:\n"
        "â€¢ à¦²à¦¿à¦‚à¦• à¦ªà§‹à¦¸à§à¦Ÿ à¦•à¦°à¦¤à§‡ à¦¯à¦¤ à¦œà¦¨à§‡à¦° à¦•à¦¾à¦›à§‡ à¦ªà¦¾à¦ à¦¾à¦¤à§‡ à¦šà¦¾à¦¨, à¦¤à¦¤ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦²à¦¾à¦—à¦¬à§‡ (à¦¯à§‡à¦®à¦¨ à§« à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ = à§« à¦œà¦¨à§‡à¦° à¦•à¦¾à¦›à§‡ à¦¯à¦¾à¦¬à§‡)à¥¤\n"
        "â€¢ à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¾ à¦²à¦¿à¦‚à¦• à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¾ à¦®à¦¾à¦¨à§à¦·à§‡à¦° à¦•à¦¾à¦›à§‡ à¦¶à§à¦§à§ à§§ à¦¬à¦¾à¦° à¦†à¦¸à¦¬à§‡, à¦à¦•à¦¬à¦¾à¦° à¦¦à§‡à¦–à¦¾ à¦²à¦¿à¦‚à¦• à¦†à¦° à¦¦à§‡à¦–à¦¾à¦¬à§‡ à¦¨à¦¾à¥¤\n"
        "â€¢ à¦¯à¦¤ à¦œà¦¨à¦•à§‡ à¦¬à¦²à§‡à¦›à¦¿à¦²à§‡à¦¨ à¦¤à¦¤à¦œà¦¨ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦«à§‡à¦²à¦²à§‡ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦¸à¦¬à¦¾à¦° à¦«à¦¿à¦¡ à¦¥à§‡à¦•à§‡ à¦šà¦²à§‡ à¦¯à¦¾à¦¬à§‡à¥¤\n"
        "â€¢ à¦•à§‡à¦‰ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦­à¦¿à¦œà¦¿à¦Ÿ à¦•à¦¨à¦«à¦¾à¦°à§à¦® à¦•à¦°à¦²à§‡ à¦¸à§‡ +0.8 à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦ªà¦¾à¦¬à§‡à¥¤\n"
        f"â€¢ à¦•à¦¾à¦‰à¦•à§‡ à¦°à§‡à¦«à¦¾à¦° à¦•à¦°à§‡ à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à¦¾à¦²à§‡ à¦†à¦ªà¦¨à¦¿ à¦¸à¦¾à¦¥à§‡ à¦¸à¦¾à¦¥à§‡ à¦ªà¦¾à¦¬à§‡à¦¨ +{REFERRAL_SIGNUP_BONUS} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ, "
        f"à¦à¦¬à¦‚ à¦¸à§‡ à¦ªà§à¦°à¦¤à¦¿à¦Ÿà¦¾ à¦Ÿà¦¾à¦¸à§à¦• à¦¸à¦®à§à¦ªà¦¨à§à¦¨ à¦•à¦°à¦²à§‡ à¦†à¦ªà¦¨à¦¿ à¦†à¦°à¦“ +{REFERRAL_TASK_COMMISSION} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦ªà¦¾à¦¬à§‡à¦¨ "
        "(Account Info à¦ à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦° à¦²à¦¿à¦‚à¦• à¦ªà¦¾à¦¬à§‡à¦¨)à¥¤\n\n"
        "à¦à¦•à¦Ÿà¦¾ à¦…à¦ªà¦¶à¦¨ à¦¬à§‡à¦›à§‡ à¦¨à¦¿à¦¨ ðŸ‘‡",
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

    db.set_referral(new_user_id, referrer_id, bonus=REFERRAL_SIGNUP_BONUS)

    await send_self_deleting_message(
        context,
        referrer_id,
        f"ðŸŽ‰ à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦° à¦²à¦¿à¦‚à¦• à¦¦à¦¿à¦¯à¦¼à§‡ à¦à¦•à¦œà¦¨ à¦¨à¦¤à§à¦¨ à¦‡à¦‰à¦œà¦¾à¦° à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à§‡à¦›à§‡!\n"
        f"à¦†à¦ªà¦¨à¦¿ à¦ªà§‡à¦¯à¦¼à§‡à¦›à§‡à¦¨ +{REFERRAL_SIGNUP_BONUS} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿà¥¤\n"
        f"(à¦¸à§‡ à¦Ÿà¦¾à¦¸à§à¦• à¦¸à¦®à§à¦ªà¦¨à§à¦¨ à¦•à¦°à¦²à§‡ à¦†à¦ªà¦¨à¦¿ à¦†à¦°à¦“ +{REFERRAL_TASK_COMMISSION} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦•à¦°à§‡ à¦ªà¦¾à¦¬à§‡à¦¨)",
    )


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
        "ðŸ‘¤ <b>Account Info</b>\n\n"
        f"Username: @{u['username']}\n"
        f"User ID: <code>{u['user_id']}</code>\n"
        f"Credit Balance: <b>{format_credit(u['credit_balance'])}</b>\n"
        f"Total Clicks (earned): <b>{u['total_clicks']}</b>\n"
        f"Total Referrals: <b>{u['total_referrals']}</b>\n\n"
        f"ðŸ”— <b>à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦° à¦²à¦¿à¦‚à¦•</b> (à¦Ÿà§à¦¯à¦¾à¦ª à¦•à¦°à¦²à§‡à¦‡ à¦•à¦ªà¦¿ à¦¹à¦¯à¦¼à§‡ à¦¯à¦¾à¦¬à§‡):\n"
        f"<code>{ref_link}</code>\n\n"
        f"à¦à¦‡ à¦²à¦¿à¦‚à¦• à¦¦à¦¿à¦¯à¦¼à§‡ à¦•à§‡à¦‰ à¦ªà§à¦°à¦¥à¦®à¦¬à¦¾à¦° à¦œà¦¯à¦¼à§‡à¦¨ à¦•à¦°à¦²à§‡ à¦†à¦ªà¦¨à¦¿ à¦ªà¦¾à¦¬à§‡à¦¨ +{REFERRAL_SIGNUP_BONUS} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ, "
        f"à¦à¦¬à¦‚ à¦¸à§‡ à¦¯à¦¤à¦¬à¦¾à¦° à¦Ÿà¦¾à¦¸à§à¦• à¦•à¦°à¦¬à§‡ à¦¤à¦¤à¦¬à¦¾à¦° +{REFERRAL_TASK_COMMISSION} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦•à¦°à§‡ à¦ªà¦¾à¦¬à§‡à¦¨à¥¤"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_keyboard(), disable_web_page_preview=True
    )


async def start_add_link(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    u = db.get_user(user_id)
    if u["credit_balance"] < 1:
        await query.edit_message_text(
            "âŒ à¦†à¦ªà¦¨à¦¾à¦° à¦•à¦¾à¦›à§‡ à¦¯à¦¥à§‡à¦·à§à¦Ÿ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦¨à§‡à¦‡à¥¤\n"
            "à¦²à¦¿à¦‚à¦• à¦ªà§‹à¦¸à§à¦Ÿ à¦•à¦°à¦¤à§‡ à¦•à¦®à¦ªà¦•à§à¦·à§‡ à§§ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦²à¦¾à¦—à¦¬à§‡à¥¤\n"
            "à¦†à¦—à§‡ \"Earn Credit\" à¦¥à§‡à¦•à§‡ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦†à¦¯à¦¼ à¦•à¦°à§à¦¨à¥¤",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    context.user_data[WAITING_FOR_LINK] = True
    await query.edit_message_text(
        f"ðŸ”— à¦†à¦ªà¦¨à¦¾à¦° Daraz à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦à¦–à¦¾à¦¨à§‡ à¦ªà¦¾à¦ à¦¾à¦¨ (à¦¶à§à¦§à§ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦®à§‡à¦¸à§‡à¦œ à¦¹à¦¿à¦¸à§‡à¦¬à§‡ à¦ªà¦¾à¦ à¦¾à¦¨)à¥¤\n\n"
        f"à¦†à¦ªà¦¨à¦¾à¦° à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨ à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸: {format_credit(u['credit_balance'])} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿà¥¤\n"
        "à¦²à¦¿à¦‚à¦• à¦ªà¦¾à¦ à¦¾à¦¨à§‹à¦° à¦ªà¦° à¦œà¦¿à¦œà§à¦žà§‡à¦¸ à¦•à¦°à¦¬ à¦•à¦¤ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦–à¦°à¦š à¦•à¦°à¦¤à§‡ à¦šà¦¾à¦¨ â€” "
        "à¦¯à¦¤ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦–à¦°à¦š à¦•à¦°à¦¬à§‡à¦¨, à¦¤à¦¤à¦œà¦¨ à¦‡à¦‰à¦¨à¦¿à¦• à¦®à¦¾à¦¨à§à¦·à§‡à¦° à¦•à¦¾à¦›à§‡ à¦à¦‡ à¦²à¦¿à¦‚à¦• à¦¦à§‡à¦–à¦¾à¦¨à§‹ à¦¹à¦¬à§‡à¥¤",
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
            "âš ï¸ à¦à¦Ÿà¦¾ à¦à¦•à¦Ÿà¦¾ à¦­à§à¦¯à¦¾à¦²à¦¿à¦¡ à¦²à¦¿à¦‚à¦• à¦®à¦¨à§‡ à¦¹à¦šà§à¦›à§‡ à¦¨à¦¾à¥¤ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ http:// à¦…à¦¥à¦¬à¦¾ https:// à¦¦à¦¿à¦¯à¦¼à§‡ à¦¶à§à¦°à§ "
            "à¦¹à¦¤à§‡ à¦¹à¦¬à§‡à¥¤ à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨, à¦…à¦¥à¦¬à¦¾ /start à¦šà¦¾à¦ªà§à¦¨ à¦®à§‡à¦¨à§à¦¤à§‡ à¦«à¦¿à¦°à§‡ à¦¯à§‡à¦¤à§‡à¥¤"
        )
        return

    u = db.get_user(user.id)
    if u["credit_balance"] < 1:
        context.user_data.pop(WAITING_FOR_LINK, None)
        await update.message.reply_text(
            "âŒ à¦¦à§à¦ƒà¦–à¦¿à¦¤, à¦†à¦ªà¦¨à¦¾à¦° à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦¶à§‡à¦· à¦¹à¦¯à¦¼à§‡ à¦—à§‡à¦›à§‡à¥¤ à¦†à¦—à§‡ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦†à¦¯à¦¼ à¦•à¦°à§à¦¨à¥¤",
            reply_markup=main_menu_keyboard(),
        )
        return

    context.user_data[PENDING_LINK_URL] = text
    context.user_data.pop(WAITING_FOR_LINK, None)
    context.user_data[WAITING_FOR_CREDIT_AMOUNT] = True

    await update.message.reply_text(
        f"ðŸ‘ à¦²à¦¿à¦‚à¦• à¦ªà¦¾à¦“à¦¯à¦¼à¦¾ à¦—à§‡à¦›à§‡à¥¤\n"
        f"à¦†à¦ªà¦¨à¦¾à¦° à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸: {format_credit(u['credit_balance'])} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿà¥¤\n\n"
        f"à¦•à¦¤ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦–à¦°à¦š à¦•à¦°à§‡ à¦ªà§‹à¦¸à§à¦Ÿ à¦•à¦°à¦¤à§‡ à¦šà¦¾à¦¨? (à§§ à¦¥à§‡à¦•à§‡ {int(u['credit_balance'])} à¦à¦° à¦®à¦§à§à¦¯à§‡ à¦à¦•à¦Ÿà¦¾ à¦¸à¦‚à¦–à§à¦¯à¦¾ à¦²à¦¿à¦–à§à¦¨)\n"
        "à¦‰à¦¦à¦¾à¦¹à¦°à¦£: 5 à¦²à¦¿à¦–à¦²à§‡ à¦à¦‡ à¦²à¦¿à¦‚à¦• à¦ à¦¿à¦• à§« à¦œà¦¨ à¦‡à¦‰à¦¨à¦¿à¦• à¦®à¦¾à¦¨à§à¦·à§‡à¦° à¦•à¦¾à¦›à§‡ à¦¦à§‡à¦–à¦¾à¦¨à§‹ à¦¹à¦¬à§‡, "
        "à§« à¦œà¦¨ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦«à§‡à¦²à¦²à§‡ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦†à¦° à¦•à¦¾à¦°à§‹ à¦•à¦¾à¦›à§‡ à¦¦à§‡à¦–à¦¾ à¦¯à¦¾à¦¬à§‡ à¦¨à¦¾à¥¤"
    )


async def receive_credit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (update.message.text or "").strip()
    url = context.user_data.get(PENDING_LINK_URL)

    if not url:
        # safety fallback, shouldn't normally happen
        context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
        await update.message.reply_text(
            "âš ï¸ à¦•à¦¿à¦›à§ à¦à¦•à¦Ÿà¦¾ à¦¸à¦®à¦¸à§à¦¯à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡, à¦†à¦¬à¦¾à¦° /start à¦šà¦¾à¦ªà§à¦¨à¥¤", reply_markup=main_menu_keyboard()
        )
        return

    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text("âš ï¸ à¦¦à¦¯à¦¼à¦¾ à¦•à¦°à§‡ à¦à¦•à¦Ÿà¦¾ à¦¸à¦ à¦¿à¦• à¦¸à¦‚à¦–à§à¦¯à¦¾ à¦²à¦¿à¦–à§à¦¨ (à¦¯à§‡à¦®à¦¨: 1, 3, 5)à¥¤")
        return

    amount = int(text)
    u = db.get_user(user.id)

    if amount > u["credit_balance"]:
        await update.message.reply_text(
            f"âš ï¸ à¦†à¦ªà¦¨à¦¾à¦° à¦•à¦¾à¦›à§‡ à¦¶à§à¦§à§ {format_credit(u['credit_balance'])} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦†à¦›à§‡à¥¤ à¦à¦° à¦¬à§‡à¦¶à¦¿ à¦¸à¦‚à¦–à§à¦¯à¦¾ à¦¦à§‡à¦“à¦¯à¦¼à¦¾ à¦¯à¦¾à¦¬à§‡ à¦¨à¦¾à¥¤ "
            "à¦†à¦¬à¦¾à¦° à¦à¦•à¦Ÿà¦¾ à¦¬à§ˆà¦§ à¦¸à¦‚à¦–à§à¦¯à¦¾ à¦²à¦¿à¦–à§à¦¨à¥¤"
        )
        return

    db.deduct_credit(user.id, amount)
    link_id = db.create_link(user.id, url, needed_clicks=amount)

    context.user_data.pop(WAITING_FOR_CREDIT_AMOUNT, None)
    context.user_data.pop(PENDING_LINK_URL, None)

    await update.message.reply_text(
        f"âœ… à¦†à¦ªà¦¨à¦¾à¦° à¦²à¦¿à¦‚à¦• à¦ªà§‹à¦¸à§à¦Ÿ à¦•à¦°à¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡! {amount} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦•à¦¾à¦Ÿà¦¾ à¦¹à¦¯à¦¼à§‡à¦›à§‡à¥¤\n"
        f"à¦à¦‡ à¦²à¦¿à¦‚à¦• à¦à¦–à¦¨ à¦ à¦¿à¦• {amount} à¦œà¦¨ à¦‡à¦‰à¦¨à¦¿à¦• à¦®à¦¾à¦¨à§à¦·à§‡à¦° à¦•à¦¾à¦›à§‡ à¦¦à§‡à¦–à¦¾à¦¨à§‹ à¦¹à¦¬à§‡à¥¤ "
        f"à¦¸à¦¬à¦¾à¦‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦«à§‡à¦²à¦²à§‡ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦¨à¦¿à¦œà§‡ à¦¥à§‡à¦•à§‡à¦‡ à¦¸à¦°à§‡ à¦¯à¦¾à¦¬à§‡à¥¤",
        reply_markup=main_menu_keyboard(),
    )


async def show_earn_credit(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    link = db.get_next_link_for_user(user_id)
    if not link:
        await query.edit_message_text(
            "ðŸ˜” à¦à¦‡ à¦®à§à¦¹à§‚à¦°à§à¦¤à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à¦¾à¦° à¦®à¦¤à§‹ à¦•à§‹à¦¨à§‹ à¦¨à¦¤à§à¦¨ à¦²à¦¿à¦‚à¦• à¦¨à§‡à¦‡à¥¤ à¦•à¦¿à¦›à§à¦•à§à¦·à¦£ à¦ªà¦° à¦†à¦¬à¦¾à¦° à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨à¥¤",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    context.user_data[PENDING_LINK_ID] = link["id"]

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ðŸ”— à¦²à¦¿à¦‚à¦• à¦­à¦¿à¦œà¦¿à¦Ÿ à¦•à¦°à§à¦¨", url=link["url"])],
            [InlineKeyboardButton("âœ… à¦­à¦¿à¦œà¦¿à¦Ÿ à¦•à¦°à§‡à¦›à¦¿, à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦¨à¦¿à¦¨", callback_data="claim_credit")],
            [InlineKeyboardButton("â¬…ï¸ Back to Menu", callback_data="menu_home")],
        ]
    )
    await query.edit_message_text(
        "ðŸ’° <b>Earn Credit</b>\n\n"
        f"à¦¨à¦¿à¦šà§‡à¦° à¦²à¦¿à¦‚à¦•à§‡ à¦•à§à¦²à¦¿à¦• à¦•à¦°à§‡ à¦­à¦¿à¦œà¦¿à¦Ÿ à¦•à¦°à§à¦¨, à¦¤à¦¾à¦°à¦ªà¦° \"à¦­à¦¿à¦œà¦¿à¦Ÿ à¦•à¦°à§‡à¦›à¦¿\" à¦¬à¦¾à¦Ÿà¦¨à§‡ à¦šà¦¾à¦ªà§à¦¨ +{format_credit(EARN_CREDIT_REWARD)} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦ªà§‡à¦¤à§‡à¥¤",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def claim_credit(query, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    link_id = context.user_data.get(PENDING_LINK_ID)
    if not link_id:
        await query.edit_message_text(
            "âš ï¸ à¦•à§‹à¦¨à§‹ à¦ªà§‡à¦¨à§à¦¡à¦¿à¦‚ à¦²à¦¿à¦‚à¦• à¦ªà¦¾à¦“à¦¯à¦¼à¦¾ à¦¯à¦¾à¦¯à¦¼à¦¨à¦¿à¥¤ à¦†à¦¬à¦¾à¦° \"Earn Credit\" à¦¥à§‡à¦•à§‡ à¦šà§‡à¦·à§à¦Ÿà¦¾ à¦•à¦°à§à¦¨.",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    link = db.get_link(link_id)
    if not link or link["status"] != "active":
        await query.edit_message_text(
            "âš ï¸ à¦à¦‡ à¦²à¦¿à¦‚à¦•à¦Ÿà¦¾ à¦†à¦° available à¦¨à§‡à¦‡ (à¦¹à¦¯à¦¼à¦¤à§‹ à¦…à¦¨à§à¦¯ à¦•à§‡à¦‰ à¦†à¦—à§‡à¦‡ à¦•à§à¦²à§‡à¦‡à¦® à¦•à¦°à§‡à¦›à§‡)à¥¤",
            reply_markup=back_to_menu_keyboard(),
        )
        context.user_data.pop(PENDING_LINK_ID, None)
        return

    inserted = db.record_click(link_id, user_id)
    if not inserted:
        await query.edit_message_text(
            "âš ï¸ à¦†à¦ªà¦¨à¦¿ à¦‡à¦¤à¦¿à¦®à¦§à§à¦¯à§‡ à¦à¦‡ à¦²à¦¿à¦‚à¦•à§‡à¦° à¦œà¦¨à§à¦¯ à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦¨à¦¿à¦¯à¦¼à§‡à¦›à§‡à¦¨à¥¤",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    # bump this link's click count; it auto-completes once needed_clicks is reached
    db.increment_link_click_and_maybe_complete(link_id)
    db.add_credit(user_id, EARN_CREDIT_REWARD)
    db.increment_total_clicks(user_id)
    context.user_data.pop(PENDING_LINK_ID, None)

    u = db.get_user(user_id)

    # pay the referrer a small commission every time their referral completes a task
    if u["referred_by"]:
        db.add_credit(u["referred_by"], REFERRAL_TASK_COMMISSION)
        await send_self_deleting_message(
            context,
            u["referred_by"],
            f"ðŸ’° à¦†à¦ªà¦¨à¦¾à¦° à¦°à§‡à¦«à¦¾à¦° à¦•à¦°à¦¾ à¦à¦•à¦œà¦¨ à¦‡à¦‰à¦œà¦¾à¦° à¦à¦•à¦Ÿà¦¾ à¦Ÿà¦¾à¦¸à§à¦• à¦¸à¦®à§à¦ªà¦¨à§à¦¨ à¦•à¦°à§‡à¦›à§‡!\n"
            f"à¦†à¦ªà¦¨à¦¿ à¦ªà§‡à¦¯à¦¼à§‡à¦›à§‡à¦¨ +{REFERRAL_TASK_COMMISSION} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿà¥¤",
        )

    await query.edit_message_text(
        f"ðŸŽ‰ à¦…à¦­à¦¿à¦¨à¦¨à§à¦¦à¦¨! à¦†à¦ªà¦¨à¦¿ +{format_credit(EARN_CREDIT_REWARD)} à¦•à§à¦°à§‡à¦¡à¦¿à¦Ÿ à¦ªà§‡à¦¯à¦¼à§‡à¦›à§‡à¦¨à¥¤\n"
        f"à¦¬à¦°à§à¦¤à¦®à¦¾à¦¨ à¦¬à§à¦¯à¦¾à¦²à§‡à¦¨à§à¦¸: <b>{format_credit(u['credit_balance'])}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_keyboard(),
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("âŒ à¦à¦‡ à¦•à¦®à¦¾à¦¨à§à¦¡ à¦¶à§à¦§à§ à¦…à§à¦¯à¦¾à¦¡à¦®à¦¿à¦¨à§‡à¦° à¦œà¦¨à§à¦¯à¥¤")
        return

    stats = db.get_admin_stats()
    top_credit = db.get_top_users_by_credit(5)
    top_ref = db.get_top_referrers(5)

    text = (
        "ðŸ›  <b>Admin Panel</b>\n\n"
        f"ðŸ‘¥ Total Users: <b>{stats['total_users']}</b>\n"
        f"ðŸ”— Total Links Posted: <b>{stats['total_links']}</b>\n"
        f"   â”œ Active: {stats['active_links']}\n"
        f"   â”” Completed: {stats['done_links']}\n"
        f"ðŸ’° Total Credits (circulation): <b>{format_credit(stats['total_credits'])}</b>\n"
        f"ðŸ‘† Total Clicks: <b>{stats['total_clicks']}</b>\n"
        f"ðŸŽ Total Referrals: <b>{stats['total_referrals']}</b>\n\n"
        "ðŸ† <b>Top 5 by Credit:</b>\n"
    )
    if top_credit:
        for i, u in enumerate(top_credit, 1):
            text += f"{i}. @{u['username']} (<code>{u['user_id']}</code>) â€” {format_credit(u['credit_balance'])} credit\n"
    else:
        text += "à¦•à§‹à¦¨à§‹ à¦‡à¦‰à¦œà¦¾à¦° à¦¨à§‡à¦‡ à¦à¦–à¦¨à§‹à¥¤\n"

    text += "\nðŸŽ¯ <b>Top Referrers:</b>\n"
    if top_ref:
        for i, u in enumerate(top_ref, 1):
            text += f"{i}. @{u['username']} (<code>{u['user_id']}</code>) â€” {u['total_referrals']} referrals\n"
    else:
        text += "à¦à¦–à¦¨à§‹ à¦•à§‡à¦‰ à¦•à¦¾à¦‰à¦•à§‡ à¦°à§‡à¦«à¦¾à¦° à¦•à¦°à§‡à¦¨à¦¿à¥¤\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("âŒ à¦à¦‡ à¦•à¦®à¦¾à¦¨à§à¦¡ à¦¶à§à¦§à§ à¦…à§à¦¯à¦¾à¦¡à¦®à¦¿à¦¨à§‡à¦° à¦œà¦¨à§à¦¯à¥¤")
        return

    if not context.args:
        await update.message.reply_text(
            "à¦¬à§à¦¯à¦¬à¦¹à¦¾à¦°: /broadcast à¦†à¦ªà¦¨à¦¾à¦° à¦®à§‡à¦¸à§‡à¦œ à¦à¦–à¦¾à¦¨à§‡ à¦²à¦¿à¦–à§à¦¨\n\n"
            "à¦‰à¦¦à¦¾à¦¹à¦°à¦£: /broadcast à¦¨à¦¤à§à¦¨ à¦«à¦¿à¦šà¦¾à¦° à¦à¦¸à§‡à¦›à§‡, à¦šà§‡à¦• à¦•à¦°à§‡ à¦¦à§‡à¦–à§à¦¨!"
        )
        return

    message_text = " ".join(context.args)
    user_ids = db.get_all_user_ids()

    status_msg = await update.message.reply_text(
        f"ðŸ“¤ {len(user_ids)} à¦œà¦¨ à¦‡à¦‰à¦œà¦¾à¦°à¦•à§‡ à¦®à§‡à¦¸à§‡à¦œ à¦ªà¦¾à¦ à¦¾à¦¨à§‹ à¦¹à¦šà§à¦›à§‡..."
    )

    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await context.bot.send_message(uid, message_text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # small delay to avoid hitting Telegram's rate limits

    await status_msg.edit_text(
        f"âœ… Broadcast à¦¸à¦®à§à¦ªà¦¨à§à¦¨!\nà¦ªà¦¾à¦ à¦¾à¦¨à§‹ à¦¹à¦¯à¦¼à§‡à¦›à§‡: {sent}\nà¦¬à§à¦¯à¦°à§à¦¥ à¦¹à¦¯à¦¼à§‡à¦›à§‡: {failed}"
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "à¦¬à§à¦à¦¤à§‡ à¦ªà¦¾à¦°à¦¿à¦¨à¦¿à¥¤ /start à¦šà¦¾à¦ªà§à¦¨ à¦®à§‡à¦¨à§ à¦¦à§‡à¦–à¦¤à§‡à¥¤"
    )


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit(
            "BOT_TOKEN à¦¸à§‡à¦Ÿ à¦•à¦°à¦¾ à¦¹à¦¯à¦¼à¦¨à¦¿à¥¤ export BOT_TOKEN='your:token' à¦šà¦¾à¦²à¦¿à¦¯à¦¼à§‡ à¦†à¦¬à¦¾à¦° à¦°à¦¾à¦¨ à¦•à¦°à§à¦¨à¥¤"
        )

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(menu_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link_message))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    logger.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
