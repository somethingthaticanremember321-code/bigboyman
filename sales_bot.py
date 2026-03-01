import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

SALES_BOT_TOKEN = os.getenv("SALES_BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7340549633"))
VIP_GROUP_ID = os.getenv("VIP_GROUP_ID", "-1002488088068")
STRIPE_PAYMENT_URL = os.getenv("STRIPE_PAYMENT_URL", "https://buy.stripe.com/test_fyc5kx0Wk8Wk8Wk8Wk")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🏎️ **Welcome to Doha Deal Sniper VIP!** 🏎️\n\n"
        "Get instant alerts for the best car deals in Qatar before anyone else sees them.\n\n"
        "💳 **Subscription**: 150 QAR / month\n"
        "Ready to join the elite? Use the button below to subscribe securely via Stripe!"
    )
    keyboard = [
        [InlineKeyboardButton("💳 Pay via Stripe", url=STRIPE_PAYMENT_URL)],
        [InlineKeyboardButton("✅ I've Paid! Join Group", callback_data="request_invite")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def request_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    username = user.username or user.first_name
    
    # Notify Admin that someone says they paid
    keyboard = [
        [
            InlineKeyboardButton("✅ Send Invite Link", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user.id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=(
            f"💰 **New Subscription Checkout!**\n\n"
            f"👤 **User**: @{username}\n"
            f"🆔 **ID**: `{user.id}`\n\n"
            f"They claim to have finished the Stripe payment. Check your Stripe Dashboard and send them the link if it's there!"
        ),
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    await query.message.reply_text(
        "⏳ **Hold tight!**\n\n"
        "I've notified our team. Once we see your payment in our Stripe dashboard, I'll send you your exclusive invite link right here!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    user_id = update.message.from_user.id
    username = update.message.from_user.username or user_id
    text = update.message.text
    
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("⏳ Verifying your voucher code... Please wait for an admin to approve.")
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🧾 **New Subscription Request**\nUser: @{username} ({user_id})\nCode: `{text}`",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = data.split("_")[1]
    
    if data.startswith("approve_"):
        invite_link = await context.bot.create_chat_invite_link(
            chat_id=VIP_GROUP_ID,
            member_limit=1
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 **Payment Approved!** 🎉\n\nWelcome to the VIP club!\nHere is your exclusive, one-time invite link:\n{invite_link.invite_link}",
            parse_mode="Markdown"
        )
        await query.edit_message_text(text=f"{query.message.text}\n\n✅ Approved.")
    elif data.startswith("reject_"):
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Your voucher code was invalid or could not be verified. Please try again or contact support."
        )
        await query.edit_message_text(text=f"{query.message.text}\n\n❌ Rejected.")

async def setup_sales_bot():
    if not SALES_BOT_TOKEN:
        print("[WARNING] SALES_BOT_TOKEN not set. Sales bot disabled.")
        return None
        
    print("[INFO] Initializing Sales Bot (Safe Mode)...")
    try:
        app = ApplicationBuilder().token(SALES_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CallbackQueryHandler(request_invite, pattern="^request_invite$"))
        app.add_handler(CallbackQueryHandler(button_handler, pattern="^(approve|reject)_"))
        
        # We do NOT await app.initialize() here to avoid crashing on DNS issues.
        # We will initialize it lazily when the first webhook arrives.
        return app
    except Exception as e:
        print(f"[SALES BOT INIT ERR] {e}")
        return None

async def handle_webhook_update(update_data: dict, app):
    """Processes a single update from Telegram via webhook."""
    try:
        if not app._initialized:
            print("[INFO] Lazily initializing Sales Bot Application...")
            await app.initialize()
            
        update = Update.de_json(update_data, app.bot)
        await app.process_update(update)
    except Exception as e:
        print(f"[TG WEBHOOK ERR] {e}")
