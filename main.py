import logging
import psycopg 
import os 
import asyncio
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
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
from psycopg.rows import dict_row 

# --- تنظیمات ضروری ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
YOUR_CHAT_ID = int(os.environ.get("YOUR_CHAT_ID", "0")) 
DATABASE_URL = os.environ.get("DATABASE_URL") 
# --- ---

MAX_LEITNER_BOX = 5 
AWAITING_REVIEW_COUNT = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# =================================================================
# متن راهنما (بدون تغییر)
# =================================================================
HELP_MESSAGE_TEXT = """
🧠 <b>این ربات چطور به حافظه شما کمک می‌کنه؟</b>

سلام! من ربات دستیار حافظه شما هستم.
حتماً براتون پیش اومده که نکته‌ای رو یاد بگیرید، یادداشت کنید، ولی چند هفته بعد کاملاً فراموشش کنید.

<b>مشکل کجاست؟</b>
مغز ما برای فراموش کردن طراحی شده! مگر اینکه اطلاعات رو در زمان‌های مشخصی "مرور" کنیم.

<b>راه حل: سیستم لایتنر (Leitner System)</b>
این ربات بر اساس یک روش علمی و مشهور به نام «سیستم جعبه لایتنر» یا «تکرار فاصله‌دار» (Spaced Repetition) کار می‌کنه.

---

📥 <b>۱. چطور ازش استفاده کنم؟</b>

خیلی ساده‌ست!
<b>هر چیزی</b> (متن، عکس، فایل، ویس، لینک) که می‌خواید به خاطر بسپارید رو مستقیماً برای من بفرستید.

من اون رو در <b>"جعبه ۱"</b> شما قرار می‌دم.

---

🔁 <b>۲. "مرور روزانه" چیه؟</b>

من هر روز (یا هر وقت دکمه «🎲 مرور روزانه» رو بزنید) چندتا از یادداشت‌هاتون رو براتون می‌فرستم و می‌پرسم:

• <b>"✅ یادم بود"</b>: عالی! اون یادداشت به جعبه بعدی می‌ره (مثلاً از ۱ به ۲).
• <b>"🤔 مرور مجدد"</b>: اشکالی نداره! اون یادداشت به "جعبه ۱" برمی‌گرده تا بیشتر مرورش کنیم.

---

📈 <b>۳. جادوی کار کجاست؟</b>

• یادداشت‌های <b>جعبه ۱</b> (چیزایی که تازه یاد گرفتید) <b>زود به زود</b> مرور می‌شن.
• یادداشت‌های <b>جعبه ۵</b> (چیزایی که کاملاً بلدید) <b>دیر به دیر</b> (مثلاً هر چند ماه) مرور می‌شن.

اینطوری، شما وقتتون رو روی چیزایی که بلدید تلف نمی‌کنید و روی چیزایی که یادتون می‌ره تمرکز می‌کنید. این کار، اطلاعات رو به حافظه بلندمدت شما منتقل می‌کنه.

---

❓ <b>معنی دکمه‌ها:</b>

• <b>🎲 مرور روزانه</b>: شروع یک جلسه مرور دستی.
• <b>📊 آمار لایتنر</b>: ببینید در هر جعبه چندتا یادداشت دارید.
• <b>📚 نمایش همه</b>: تمام یادداشت‌هایی که ذخیره کردید رو براتون فوروارد می‌کنه.
• <b>⚙️ تنظیمات</b>: تعداد مرورهای روزانه رو می‌تونید کم یا زیاد کنید.
• <b>❓ راهنما</b>: همین پیامی که دارید می‌خونید!

<b>برای شروع، اولین نکته‌ای که می‌خواید یادتون بمونه رو برای من بفرستید.</b>
"""


# =================================================================
# بخش دیتابیس (بدون تغییر)
# =================================================================

def get_db_conn():
    try:
        conn = psycopg.connect(DATABASE_URL)
        return conn
    except psycopg.OperationalError as e:
        logger.error(f"FATAL: Could not connect to PostgreSQL database: {e}")
        return None

def init_db():
    conn = get_db_conn()
    if not conn: return
    
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            leitner_box INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id, message_id)
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            user_id BIGINT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY(user_id, key)
        );
        """)
        conn.commit()
    conn.close()
    logger.info("Database PostgreSQL initialized for Multi-User Leitner system.")

def add_message_id_to_db(user_id: int, chat_id: int, message_id: int):
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("""
            INSERT INTO messages (user_id, chat_id, message_id, leitner_box) VALUES (%s, %s, %s, 1)
            ON CONFLICT (user_id, message_id) DO NOTHING;
            """, (user_id, chat_id, message_id))
            conn.commit()
        conn.close()
        return True
    except psycopg.Error as e:
        logger.error(f"Database error in add_message_id_to_db: {e}")
        return False

def get_leitner_stats(user_id: int) -> dict:
    stats = {f"box_{i}": 0 for i in range(1, MAX_LEITNER_BOX + 1)}
    total = 0
    try:
        conn = get_db_conn()
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT leitner_box, COUNT(*) as count FROM messages WHERE user_id = %s GROUP BY leitner_box", (user_id,))
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

def move_leitner_box(user_id: int, message_id: int, direction: str) -> int:
    new_box = 0
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            if direction == 'up':
                cursor.execute(f"""
                UPDATE messages 
                SET leitner_box = LEAST(leitner_box + 1, {MAX_LEITNER_BOX}) 
                WHERE user_id = %s AND message_id = %s
                RETURNING leitner_box;
                """, (user_id, message_id))
            elif direction == 'reset':
                cursor.execute("""
                UPDATE messages SET leitner_box = 1 
                WHERE user_id = %s AND message_id = %s
                RETURNING leitner_box;
                """, (user_id, message_id))
            
            result = cursor.fetchone()
            new_box = result[0] if result else 0
            conn.commit()
        conn.close()
        return new_box
    except psycopg.Error as e:
        logger.error(f"Database error in move_leitner_box: {e}")
        return 0

def get_setting(user_id: int, key: str, default: str) -> str:
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE user_id = %s AND key = %s", (user_id, key))
            result = cursor.fetchone()
        conn.close()
        if not result:
            set_setting(user_id, key, default)
            return default
        return result[0]
    except psycopg.Error as e:
        logger.error(f"Database error in get_setting: {e}")
        return default

def set_setting(user_id: int, key: str, value: str):
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("""
            INSERT INTO settings (user_id, key, value) VALUES (%s, %s, %s)
            ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value;
            """, (user_id, key, value))
            conn.commit()
        conn.close()
        logger.info(f"Setting '{key}' for user '{user_id}' updated to '{value}'.")
    except psycopg.Error as e:
        logger.error(f"Database error in set_setting: {e}")

def get_messages_in_box(user_id: int, box_number: int) -> list:
    messages = []
    try:
        conn = get_db_conn()
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT message_id, chat_id FROM messages WHERE user_id = %s AND leitner_box = %s ORDER BY id ASC", (user_id, box_number))
            messages = cursor.fetchall()
        conn.close()
        return messages
    except psycopg.Error as e:
        logger.error(f"Database error in get_messages_in_box: {e}")
        return []

def get_all_messages_for_user(user_id: int) -> list:
    messages = []
    try:
        conn = get_db_conn()
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT message_id, chat_id FROM messages WHERE user_id = %s ORDER BY id ASC", (user_id,))
            messages = cursor.fetchall()
        conn.close()
        return messages
    except psycopg.Error as e:
        logger.error(f"Database error in get_all_messages_for_user: {e}")
        return []

def get_all_users_for_review() -> list:
    users = []
    try:
        conn = get_db_conn()
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT DISTINCT user_id, chat_id FROM messages")
            users = cursor.fetchall()
        conn.close()
        return users
    except psycopg.Error as e:
        logger.error(f"Database error in get_all_users_for_review: {e}")
        return []

def delete_message_from_db(user_id: int, message_id: int) -> bool:
    try:
        conn = get_db_conn()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM messages WHERE user_id = %s AND message_id = %s", (user_id, message_id))
            conn.commit()
        conn.close()
        logger.info(f"Message {message_id} deleted for user {user_id}.")
        return True
    except psycopg.Error as e:
        logger.error(f"Database error in delete_message_from_db: {e}")
        return False


# =================================================================
# دستورات و دکمه‌های اصلی (تابع start اصلاح شد)
# =================================================================

# <--- تابع start با parse_mode=HTML اصلاح شد --->
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    keyboard = [
        ["🎲 مرور روزانه", "📊 آمار لایتنر"],
        ["📚 نمایش همه", "⚙️ تنظیمات"],
        ["❓ راهنما"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    stats = get_leitner_stats(user_id) 
    
    # <--- متن خوشامدگویی به فرمت HTML تغییر کرد --->
    welcome_message = (
        f"سلام! به سیستم یادآوری لایتنر شخصی خود خوش آمدید.\n\n"
        f"شما <b>{stats['total']}</b> یادداشت در آرشیو خود دارید.\n\n"
        "هر چیزی برای من بفرستید تا به <b>جعبه ۱</b> لایتنر شما اضافه شود.\n\n"
        "اگر نمی‌دانید چطور کار می‌کند، دکمه <b>«❓ راهنما»</b> را بزنید."
    )
    
    # <--- parse_mode به HTML تغییر کرد تا خطا برطرف شود --->
    await update.message.reply_text(
        welcome_message, 
        reply_markup=reply_markup, 
        parse_mode=ParseMode.HTML
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        HELP_MESSAGE_TEXT, 
        parse_mode=ParseMode.HTML, 
        disable_web_page_preview=True
    )

async def handle_new_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = update.message.message_id
    
    if add_message_id_to_db(user_id, chat_id, message_id):
        stats = get_leitner_stats(user_id) 
        await update.message.reply_text(f"✅ به جعبه ۱ شما اضافه شد! (مجموع: {stats['total']})", reply_to_message_id=message_id)

# =================================================================
# منطق اصلی مرور و بازخورد لایتنر (بدون تغییر)
# =================================================================

async def trigger_leitner_review(bot, user_id: int, chat_id: int) -> int:
    daily_reviews = int(get_setting(user_id, 'daily_reviews', '2'))
    logger.info(f"Triggering {daily_reviews} Leitner reviews for user {user_id}...")
    
    conn = get_db_conn()
    if not conn: return 0
    
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT message_id, chat_id FROM messages WHERE user_id = %s ORDER BY leitner_box ASC, RANDOM() LIMIT %s", (user_id, daily_reviews))
        messages = cursor.fetchall()
    conn.close()
    
    if not messages: return 0

    for msg in messages:
        message_id = msg['message_id']
        from_chat_id = msg['chat_id'] 
        
        keyboard = [[
            InlineKeyboardButton("✅ یادم بود", callback_data=f"leitner_up_{message_id}"),
            InlineKeyboardButton("🤔 مرور مجدد", callback_data=f"leitner_reset_{message_id}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"leitner_del_{message_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id, reply_markup=reply_markup)
        except BadRequest as e:
            logger.error(f"Failed to copy message {message_id} for user {user_id} (BadRequest): {e}. It might be deleted.")
        except Exception as e:
            logger.error(f"An unexpected error occurred while copying message {message_id} for user {user_id}: {e}")
            
    return len(messages)

async def trigger_daily_reviews_for_all_users(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Running daily review job for ALL users...")
    bot = context.bot
    users = get_all_users_for_review() 
    
    if not users:
        logger.info("No users with messages found to review.")
        return

    logger.info(f"Found {len(users)} users to review.")
    for user in users:
        try:
            await trigger_leitner_review(bot, user['user_id'], user['chat_id'])
            await asyncio.sleep(1) 
        except Exception as e:
            logger.error(f"Failed to trigger review for user {user['user_id']}: {e}")


async def handle_leitner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    try:
        data_part = query.data.replace("leitner_", "", 1)
        parts = data_part.rsplit("_", 1)
        action = parts[0]
        message_id = int(parts[1])

    except (ValueError, IndexError, TypeError):
        logger.error(f"Invalid callback data received: {query.data}")
        await query.edit_message_text(text="❌ خطای داخلی.")
        return

    feedback_text = ""
    new_keyboard = None 

    if action == "up":
        new_box = move_leitner_box(user_id, message_id, 'up')
        feedback_text = f"👍 عالی! این یادداشت به جعبه <b>{new_box}</b> منتقل شد."
    
    elif action == "reset":
        new_box = move_leitner_box(user_id, message_id, 'reset')
        feedback_text = f"🔄 این یادداشت برای مرور بیشتر به جعبه <b>{new_box}</b> برگشت."
    
    elif action == "del":
        feedback_text = "⚠️ <b>آیا از حذف این یادداشت مطمئن هستید؟</b>\nاین عمل قابل بازگشت نیست."
        new_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚮 بله، حذف کن", callback_data=f"leitner_del_confirm_{message_id}"),
                InlineKeyboardButton("↪️ انصراف", callback_data=f"leitner_del_cancel_{message_id}")
            ]
        ])

    elif action == "del_confirm":
        if delete_message_from_db(user_id, message_id):
            feedback_text = "🗑️ یادداشت برای همیشه حذف شد."
        else:
            feedback_text = "❌ خطایی در هنگام حذف رخ داد."

    elif action == "del_cancel":
        feedback_text = "عملیات حذف لغو شد."
        new_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ یادم بود", callback_data=f"leitner_up_{message_id}"),
                InlineKeyboardButton("🤔 مرور مجدد", callback_data=f"leitner_reset_{message_id}"),
                InlineKeyboardButton("🗑️ حذف", callback_data=f"leitner_del_{message_id}")
            ]
        ])

    else:
        feedback_text = "❌ دستور نامعتبر."

    try:
        if action == "del_confirm" and "حذف شد" in feedback_text:
            await query.delete_message()
        else:
            if query.message.text:
                await query.edit_message_text(
                    text=feedback_text, 
                    parse_mode=ParseMode.HTML, 
                    reply_markup=new_keyboard
                )
            else:
                await query.edit_message_caption(
                    caption=feedback_text, 
                    parse_mode=ParseMode.HTML, 
                    reply_markup=new_keyboard
                )
    except BadRequest as e:
        if "message is not modified" not in str(e):
            logger.warning(f"Could not edit message after callback: {e}")
    except Exception as e:
        logger.error(f"Failed to edit/delete message after callback: {e}")


# =================================================================
# منوی آمار (بدون تغییر)
# =================================================================

async def stats_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    stats = get_leitner_stats(user_id) 
    
    keyboard = []
    for i in range(1, MAX_LEITNER_BOX + 1):
        box_count = stats[f'box_{i}']
        button_text = f"📦 جعبه {i} ({box_count} مورد)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"view_box_{i}")])
    
    keyboard.append([InlineKeyboardButton("❌ بستن", callback_data="stats_close")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    stats_text = f"📊 <b>آمار جعبه لایتنر شما</b>\n\nبرای مشاهده محتوای هر جعبه، روی دکمه آن کلیک کنید."

    if update.message:
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    elif update.callback_query:
        try:
            await update.callback_query.message.edit_text(stats_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except BadRequest:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=stats_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)


async def handle_view_box_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    try:
        box_number = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطای داخلی در انتخاب جعبه.")
        return

    messages = get_messages_in_box(user_id, box_number)
    
    await query.edit_message_text(f"شما <b>{len(messages)}</b> یادداشت در جعبه {box_number} دارید. در حال ارسال...", parse_mode=ParseMode.HTML)
    
    if not messages:
        await query.delete_message()
        await stats_menu_handler(update, context) 
        return

    await query.delete_message()

    for msg in messages:
        message_id = msg['message_id']
        from_chat_id = msg['chat_id']
        
        keyboard = [[
            InlineKeyboardButton("✅ یادم بود", callback_data=f"leitner_up_{message_id}"),
            InlineKeyboardButton("🤔 مرور مجدد", callback_data=f"leitner_reset_{message_id}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"leitner_del_{message_id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.copy_message(
                chat_id=from_chat_id, 
                from_chat_id=from_chat_id,
                message_id=message_id,
                reply_markup=reply_markup
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Could not copy message {message_id} from box view for user {user_id}: {e}")

    await stats_menu_handler(update, context)


async def handle_stats_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.delete_message()
    except Exception as e:
        logger.warning(f"Could not delete stats message: {e}")

# =================================================================
# سایر دکمه‌ها و مکالمه تنظیمات (بدون تغییر)
# =================================================================

async def handle_review_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    daily_reviews = int(get_setting(user_id, 'daily_reviews', '2'))
    
    await update.message.reply_text(f"⏳ در حال یافتن <b>{daily_reviews}</b> یادداشت برای مرور...", parse_mode=ParseMode.HTML)
    sent_count = await trigger_leitner_review(context.bot, user_id, chat_id)
    if sent_count == 0: await update.message.reply_text("هنوز هیچ یادداشتی برای مرور ذخیره نکرده‌اید!")

async def list_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await update.message.reply_text("⏳ در حال دریافت لیست کامل یادداشت‌ها...")
    
    all_messages = get_all_messages_for_user(user_id)
    
    if not all_messages:
        await update.message.reply_text("هنوز هیچ یادداشتی ذخیره نکرده‌اید!")
        return

    await update.message.reply_text(f"در حال ارسال <b>{len(all_messages)}</b> یادداشت...", parse_mode=ParseMode.HTML)
    for msg in all_messages:
        try:
            await context.bot.forward_message(chat_id=chat_id, from_chat_id=msg['chat_id'], message_id=msg['message_id'])
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Could not forward message {msg['message_id']} for user {user_id}: {e}")
    await update.message.reply_text("✅ ارسال تمام شد.")

async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    current_count = get_setting(user_id, 'daily_reviews', '2')
    
    await update.message.reply_text(
        f"⚙️ <b>تنظیمات</b>\n\nتعداد مرور روزانه در حال حاضر: <b>{current_count}</b>\n\n"
        "لطفاً تعداد جدید را به صورت یک عدد (مثلاً 5) ارسال کنید.\nبرای لغو، /cancel را بزنید.",
        parse_mode=ParseMode.HTML
    )
    return AWAITING_REVIEW_COUNT

async def settings_receive_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        new_count = int(update.message.text)
        if 1 <= new_count <= 20:
            set_setting(user_id, 'daily_reviews', str(new_count))
            await update.message.reply_text(f"✅ تعداد مرور روزانه به <b>{new_count}</b> تغییر کرد.", parse_mode=ParseMode.HTML)
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
# تابع اصلی (بدون تغییر)
# =================================================================
def main() -> None:
    if not BOT_TOKEN:
        logger.error("FATAL: Missing environment variable: BOT_TOKEN")
        return
    if not DATABASE_URL:
        logger.error("FATAL: Missing environment variable: DATABASE_URL")
        return
        
    init_db() 
    application = Application.builder().token(BOT_TOKEN).build()

    private_chat_filter = filters.ChatType.PRIVATE

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^⚙️ تنظیمات$") & private_chat_filter, settings_start)],
        states={AWAITING_REVIEW_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & private_chat_filter, settings_receive_count)],},
        fallbacks=[CommandHandler("cancel", settings_cancel, filters=private_chat_filter)],
    )
    application.add_handler(conv_handler)

    application.add_handler(CommandHandler("start", start, filters=private_chat_filter))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^🎲 مرور روزانه$") & private_chat_filter, handle_review_button))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📊 آمار لایتنر$") & private_chat_filter, stats_menu_handler))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📚 نمایش همه$") & private_chat_filter, list_all_messages))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex("^❓ راهنما$") & private_chat_filter, show_help))
    
    application.add_handler(CallbackQueryHandler(handle_leitner_callback, pattern="^leitner_"))
    application.add_handler(CallbackQueryHandler(handle_view_box_callback, pattern="^view_box_"))
    application.add_handler(CallbackQueryHandler(handle_stats_close_callback, pattern="^stats_close$"))

    button_texts = ["^🎲 مرور روزانه$", "^📊 آمار لایتنر$", "^📚 نمایش همه$", "^⚙️ تنظیمات$", "^❓ راهنما$"]
    button_regex = "|".join(button_texts)
    application.add_handler(MessageHandler(
        filters.ALL & (~filters.COMMAND) & (~filters.Regex(button_regex)) & private_chat_filter,
        handle_new_message
    ))

    job_queue = application.job_queue
    job_queue.run_repeating(trigger_daily_reviews_for_all_users, interval=86400, first=10) 

    logger.info("Starting Leitner System Bot (Multi-User Edition with Delete Feature)...")
    application.run_polling()

if __name__ == "__main__":
    main()