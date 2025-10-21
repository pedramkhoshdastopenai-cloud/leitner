import logging
import psycopg # درایور PostgreSQL
import os # برای خواندن متغیرهای محیطی
import asyncio
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
)
from telegram.error import BadRequest
from psycopg.rows import dict_row # برای دریافت نتایج به صورت دیکشنری

# --- تنظیمات ضروری ---
# اینها از متغیرهای محیطی Koyeb خوانده می‌شوند
BOT_TOKEN = os.environ.get("BOT_TOKEN")
YOUR_CHAT_ID = int(os.environ.get("YOUR_CHAT_ID"))
DATABASE_URL = os.environ.get("DATABASE_URL") 
# --- ---

MAX_LEITNER_BOX = 5 
AWAITING_REVIEW_COUNT = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =================================================================
# بخش دیتابیس (PostgreSQL)
# =================================================================

def get_db_conn():
    """یک کانکشن جدید به دیتابیس PostgreSQL باز می‌کند"""
    try:
        conn = psycopg.connect(DATABASE_URL)
        return conn
    except psycopg.OperationalError as e:
        logger.error(f"FATAL: Could not connect to PostgreSQL database: {e}")
        return None

def init_db():
    """جداول را در دیتابیس PostgreSQL ایجاد می‌کند"""
    conn = get_db_conn()
    if not conn: return
    
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            message_id BIGINT NOT NULL UNIQUE,
            leitner_box INTEGER NOT NULL DEFAULT 1
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        # INSERT ... ON CONFLICT ... DO NOTHING معادل INSERT OR IGNORE است
        cursor.execute("""
        INSERT INTO settings (key, value) VALUES ('daily_reviews', '2')
        ON CONFLICT (key) DO NOTHING;
        """)
        conn.commit()
    conn.close()
    logger.info("Database PostgreSQL initialized for Leitner system.")

def add_message_id_to_db(message_id: int):
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            # ON CONFLICT (message_id) DO NOTHING همان IGNORE است
            cursor.execute("""
            INSERT INTO messages (message_id, leitner_box) VALUES (%s, 1)
            ON CONFLICT (message_id) DO NOTHING;
            """, (message_id,))
            conn.commit()
        conn.close()
        return True
    except psycopg.Error as e:
        logger.error(f"Database error in add_message_id_to_db: {e}")
        return False

def get_leitner_stats() -> dict:
    stats = {f"box_{i}": 0 for i in range(1, MAX_LEITNER_BOX + 1)}
    total = 0
    try:
        conn = get_db_conn()
        # استفاده از row_factory برای دسترسی راحت‌تر به نتایج
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT leitner_box, COUNT(*) as count FROM messages GROUP BY leitner_box")
            rows = cursor.fetchall()
            for row in rows:
                if 1 <= row['leitner_box'] <= MAX_LEITNER_BOX:
                    stats[f"box_{row['leitner_box']}"] = row['count']
                    total += row['count']
        conn.close()
    except psycopg.Error as e:
        logger.error(f"Database error in get_leitner_stats: {e}")
    stats['total'] = total
    return stats

def move_leitner_box(message_id: int, direction: str) -> int:
    new_box = 0
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            if direction == 'up':
                # در PostgreSQL به جای MIN از LEAST استفاده می‌شود
                cursor.execute(f"""
                UPDATE messages 
                SET leitner_box = LEAST(leitner_box + 1, {MAX_LEITNER_BOX}) 
                WHERE message_id = %s
                RETURNING leitner_box;
                """, (message_id,))
            elif direction == 'reset':
                cursor.execute("""
                UPDATE messages SET leitner_box = 1 WHERE message_id = %s
                RETURNING leitner_box;
                """, (message_id,))
            
            result = cursor.fetchone()
            new_box = result[0] if result else 0
            conn.commit()
        conn.close()
        return new_box
    except psycopg.Error as e:
        logger.error(f"Database error in move_leitner_box: {e}")
        return 0

def get_setting(key: str, default: str) -> str:
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
            result = cursor.fetchone()
        conn.close()
        return result[0] if result else default
    except psycopg.Error as e:
        logger.error(f"Database error in get_setting: {e}")
        return default

def set_setting(key: str, value: str):
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            # معادل INSERT OR REPLACE در PostgreSQL
            cursor.execute("""
            INSERT INTO settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
            """, (key, value))
            conn.commit()
        conn.close()
        logger.info(f"Setting '{key}' updated to '{value}'.")
    except psycopg.Error as e:
        logger.error(f"Database error in set_setting: {e}")

def get_messages_in_box(box_number: int) -> list:
    messages = []
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT message_id FROM messages WHERE leitner_box = %s ORDER BY id ASC", (box_number,))
            messages = cursor.fetchall()
        conn.close()
        return messages
    except psycopg.Error as e:
        logger.error(f"Database error in get_messages_in_box: {e}")
        return []

# =================================================================
# دستورات و دکمه‌های اصلی
# =================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != YOUR_CHAT_ID: return
    
    keyboard = [
        ["🎲 مرور روزانه", "📊 آمار لایتنر"],
        ["📚 نمایش همه", "⚙️ تنظیمات"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    stats = get_leitner_stats()
    welcome_message = (
        f"سلام! به سیستم یادآوری لایتنر خوش آمدید.\n\n"
        f"شما **{stats['total']}** یادداشت در آرشیو خود دارید.\n\n"
        "هر چیزی برای من بفرستید تا به **جعبه ۱** لایتنر اضافه شود."
    )
    await update.message.reply_text(welcome_message, reply_markup=reply_markup, parse_mode='Markdown')

async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_id = update.message.message_id
    if add_message_id_to_db(message_id):
        stats = get_leitner_stats()
        await update.message.reply_text(f"✅ به جعبه ۱ اضافه شد! (مجموع: {stats['total']})", reply_to_message_id=message_id)

# =================================================================
# منطق اصلی مرور و بازخورد لایتنر
# =================================================================

async def trigger_leitner_review(bot, chat_id: int) -> int:
    daily_reviews = int(get_setting('daily_reviews', '2'))
    logger.info(f"Triggering {daily_reviews} Leitner reviews...")
    
    conn = get_db_conn()
    if not conn: return 0
    
    with conn.cursor() as cursor:
        cursor.execute("SELECT message_id FROM messages ORDER BY leitner_box ASC, RANDOM() LIMIT %s", (daily_reviews,))
        messages = cursor.fetchall()
    conn.close()
    
    if not messages: return 0

    for msg in messages:
        message_id = msg[0]
        keyboard = [[
            InlineKeyboardButton("✅ یادم بود", callback_data=f"leitner_up_{message_id}"),
            InlineKeyboardButton("🤔 مرور مجدد", callback_data=f"leitner_reset_{message_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        except BadRequest as e:
            logger.error(f"Failed to copy message {message_id} (BadRequest): {e}. It might be deleted.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while copying message {message_id}: {e}")
            
    return len(messages)

async def handle_leitner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    try:
        action, message_id_str = query.data.split("_", 2)[1:]
        message_id = int(message_id_str)
    except (ValueError, IndexError):
        logger.error(f"Invalid callback data received: {query.data}")
        await query.edit_message_text(text="❌ خطای داخلی در پردازش دکمه.")
        return

    if action == "up":
        new_box = move_leitner_box(message_id, 'up')
        feedback_text = f"👍 عالی! این یادداشت به جعبه **{new_box}** منتقل شد."
    elif action == "reset":
        new_box = move_leitner_box(message_id, 'reset')
        feedback_text = f"🔄 این یادداشت برای مرور بیشتر به جعبه **{new_box}** برگشت."
    else:
        feedback_text = "❌ دستور نامعتبر."
    
    try:
        if query.message.text:
            await query.edit_message_text(text=feedback_text, parse_mode='Markdown', reply_markup=None)
        else:
            await query.edit_message_caption(caption=feedback_text, parse_mode='Markdown', reply_markup=None)
    except BadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Could not edit message after callback (maybe already edited): {e}")
    except Exception as e:
        logger.error(f"Failed to edit message after callback: {e}")

# =================================================================
# منوی آمار تعاملی و جدید
# =================================================================

async def stats_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """منوی اصلی آمار را با دکمه‌های Inline نمایش می‌دهد"""
    stats = get_leitner_stats()
    keyboard = []
    for i in range(1, MAX_LEITNER_BOX + 1):
        box_count = stats[f'box_{i}']
        button_text = f"📦 جعبه {i} ({box_count} مورد)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"view_box_{i}")])
    
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="stats_close")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    stats_text = f"📊 **آمار جعبه لایتنر شما**\n\nبرای مشاهده محتوای هر جعبه، روی دکمه آن کلیک کنید."
    await update.message.reply_text(stats_text, parse_mode='Markdown', reply_markup=reply_markup)

async def handle_view_box_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """محتوای یک جعبه خاص را نمایش می‌دهد"""
    query = update.callback_query
    await query.answer()
    
    try:
        box_number = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطای داخلی در انتخاب جعبه.")
        return

    messages = get_messages_in_box(box_number)
    
    await query.edit_message_text(f"شما **{len(messages)}** یادداشت در جعبه {box_number} دارید. در حال ارسال...")
    
    if not messages:
        # پیام اصلی را به حالت اولیه آمار برمی‌گردانیم تا کاربر بتواند جعبه دیگری را انتخاب کند
        await stats_menu_handler(query, context) # query.message.reply_text(...)
        return

    # پیام "در حال ارسال..." را می‌بندیم تا مزاحم نباشد
    await query.delete_message()

    for msg in messages:
        message_id = msg[0]
        keyboard = [[
            InlineKeyboardButton("✅ یادم بود", callback_data=f"leitner_up_{message_id}"),
            InlineKeyboardButton("🤔 مرور مجدد", callback_data=f"leitner_reset_{message_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.copy_message(
                chat_id=YOUR_CHAT_ID,
                from_chat_id=YOUR_CHAT_ID,
                message_id=message_id,
                reply_markup=reply_markup
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Could not copy message {message_id} from box view: {e}")
            
    # پس از اتمام، منوی آمار را دوباره نمایش می‌دهیم
    # این یک تجربه کاربری بهتر است
    await stats_menu_handler(query, context)


async def handle_stats_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """منوی آمار را می‌بندد (پیام را حذف می‌کند)"""
    query = update.callback_query
    await query.answer()
    try:
        await query.delete_message()
    except Exception as e:
        logger.warning(f"Could not delete stats message: {e}")

# =================================================================
# سایر دکمه‌ها و مکالمه تنظیمات
# =================================================================

async def handle_review_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    daily_reviews = int(get_setting('daily_reviews', '2'))
    await update.message.reply_text(f"⏳ در حال یافتن **{daily_reviews}** یادداشت برای مرور...")
    sent_count = await trigger_leitner_review(context.bot, YOUR_CHAT_ID)
    if sent_count == 0: await update.message.reply_text("هنوز هیچ یادداشتی برای مرور ذخیره نکرده‌اید!")

async def list_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ در حال دریافت لیست کامل یادداشت‌ها...")
    
    conn = get_db_conn()
    if not conn:
        await update.message.reply_text("❌ خطای اتصال به دیتابیس.")
        return

    with conn.cursor() as cursor:
        cursor.execute("SELECT message_id FROM messages ORDER BY id ASC")
        all_messages = cursor.fetchall()
    conn.close()
    
    if not all_messages:
        await update.message.reply_text("هنوز هیچ یادداشتی ذخیره نکرده‌اید!")
        return

    await update.message.reply_text(f"در حال ارسال **{len(all_messages)}** یادداشت...")
    for msg in all_messages:
        try:
            await context.bot.forward_message(chat_id=YOUR_CHAT_ID, from_chat_id=YOUR_CHAT_ID, message_id=msg[0])
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Could not forward message {msg[0]}: {e}")
    await update.message.reply_text("✅ ارسال تمام شد.")

async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    current_count = get_setting('daily_reviews', '2')
    await update.message.reply_text(
        f"⚙️ **تنظیمات**\n\nتعداد مرور روزانه در حال حاضر: **{current_count}**\n\n"
        "لطفاً تعداد جدید را به صورت یک عدد (مثلاً 5) ارسال کنید.\nبرای لغو، /cancel را بزنید.",
        parse_mode='Markdown'
    )
    return AWAITING_REVIEW_COUNT

async def settings_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_count = int(update.message.text)
        if 1 <= new_count <= 20:
            set_setting('daily_reviews', str(new_count))
            await update.message.reply_text(f"✅ تعداد مرور روزانه به **{new_count}** تغییر کرد.", parse_mode='Markdown')
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ لطفاً یک عدد بین ۱ تا ۲۰ وارد کنید.")
            return AWAITING_REVIEW_COUNT
    except (ValueError, TypeError):
        await update.message.reply_text("❌ ورودی نامعتبر است. لطفاً فقط یک عدد ارسال کنید.")
        return AWAITING_REVIEW_COUNT

async def settings_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END

# =================================================================
# تابع اصلی
# =================================================================
def main() -> None:
    # چک کردن متغیرهای محیطی قبل از اجرا
    if not BOT_TOKEN:
        logger.error("FATAL: Missing environment variable: BOT_TOKEN")
        return
    if not YOUR_CHAT_ID:
        logger.error("FATAL: Missing environment variable: YOUR_CHAT_ID")
        return
    if not DATABASE_URL:
        logger.error("FATAL: Missing environment variable: DATABASE_URL")
        return
        
    init_db() # دیتابیس PostgreSQL را راه‌اندازی می‌کند
    application = Application.builder().token(BOT_TOKEN).build()
    user_filter = filters.Chat(chat_id=YOUR_CHAT_ID)

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^⚙️ تنظیمات$") & user_filter, settings_start)],
        states={AWAITING_REVIEW_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, settings_receive_count)],},
        fallbacks=[CommandHandler("cancel", settings_cancel)],
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("start", start, filters=user_filter))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🎲 مرور روزانه$") & user_filter, handle_review_button))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 آمار لایتنر$") & user_filter, stats_menu_handler))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📚 نمایش همه$") & user_filter, list_all_messages))
    
    application.add_handler(CallbackQueryHandler(handle_leitner_callback, pattern="^leitner_"))
    application.add_handler(CallbackQueryHandler(handle_view_box_callback, pattern="^view_box_"))
    application.add_handler(CallbackQueryHandler(handle_stats_close_callback, pattern="^stats_close$"))

    button_texts = ["^🎲 مرور روزانه$", "^📊 آمار لایتنر$", "^📚 نمایش همه$", "^⚙️ تنظیمات$"]
    button_regex = "|".join(button_texts)
    application.add_handler(MessageHandler(
        filters.ALL & (~filters.COMMAND) & (~filters.Regex(button_regex)) & user_filter,
        handle_new_message
    ))

    job_queue = application.job_queue
    job_queue.run_repeating(lambda ctx: trigger_leitner_review(ctx.bot, YOUR_CHAT_ID), interval=86400, first=10) # 86400 ثانیه = 1 روز

    logger.info("Starting Leitner System Bot (Pro Edition on Koyeb)...")
    application.run_polling()

if __name__ == "__main__":
    main()