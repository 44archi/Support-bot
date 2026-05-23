import logging
import sqlite3
import os
from datetime import datetime, timedelta
import asyncio

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import Forbidden, BadRequest

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6085761723  # Твой ID

# ID приватных каналов
CHANNEL_1_ID = -1003877994893  # Канал для публикации поздравлений
CHANNEL_2_ID = -1003981236439  # Второй канал

# Оба канала для проверки подписки
REQUIRED_CHANNELS = [CHANNEL_1_ID, CHANNEL_2_ID]

# ================== ЛОГИРОВАНИЕ ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================== БАЗА ДАННЫХ ==================
def init_db():
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS birthdays (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            date TEXT,
            publish_username INTEGER DEFAULT 0,
            was_published INTEGER DEFAULT 0,
            pending_republish INTEGER DEFAULT 0,
            date_changed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def save_birthday(user_id, username, date, publish, date_changed=0):
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO birthdays 
           (user_id, username, date, publish_username, was_published, pending_republish, date_changed) 
           VALUES (?, ?, ?, ?, 0, 0, ?)""",
        (user_id, username, date, publish, date_changed),
    )
    conn.commit()
    conn.close()

def get_user_birthday(user_id):
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, publish_username, date_changed FROM birthdays WHERE user_id = ?",
        (user_id,),
    )
    result = cursor.fetchone()
    conn.close()
    return result

def update_username(user_id, username):
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE birthdays SET username = ? WHERE user_id = ?",
        (username, user_id),
    )
    conn.commit()
    conn.close()

def set_published(user_id):
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE birthdays SET was_published = 1, pending_republish = 0 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()

def set_pending_republish(user_id):
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE birthdays SET pending_republish = 1 WHERE user_id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()

def get_today_birthdays():
    today = datetime.now().strftime("%d.%m")
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, username, publish_username, was_published, pending_republish FROM birthdays WHERE date = ?",
        (today,),
    )
    results = cursor.fetchall()
    conn.close()
    return results

def get_pending_republish():
    conn = sqlite3.connect("birthdays.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, username FROM birthdays WHERE pending_republish = 1"
    )
    results = cursor.fetchall()
    conn.close()
    return results

# ================== СОСТОЯНИЯ ==================
FEEDBACK_TEXT = 1
SUGGEST_TEXT = 2
BIRTHDAY_DATE = 3
BIRTHDAY_PUBLISH = 4
BIRTHDAY_CHANGE_CONFIRM = 5

# ================== КЛАВИАТУРЫ ==================
main_keyboard = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📞 Обратная связь")],
        [KeyboardButton("📝 Предложить пост"), KeyboardButton("🎂 День рождения")],
    ],
    resize_keyboard=True,
)

publish_keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("✅ Да, опубликовать мой ник", callback_data="publish_yes"),
            InlineKeyboardButton("❌ Нет, оставить в секрете", callback_data="publish_no"),
        ]
    ]
)

def get_check_subscription_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Проверить подписку", callback_data="check_subscription")]]
    )

def get_republish_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 Проверить и опубликовать", callback_data="republish")]]
    )

change_date_keyboard = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("⚠️ Да, я понимаю — сменить дату", callback_data="change_date_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="change_date_cancel"),
        ]
    ]
)

# ================== ПРОВЕРКА ПОДПИСКИ ==================
async def check_user_subscription(user_id, context):
    if not REQUIRED_CHANNELS:
        return True

    for channel_id in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(
                chat_id=channel_id, user_id=user_id
            )
            if member.status in ("left", "kicked", "banned"):
                return False
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Ошибка проверки подписки на канал {channel_id}: {e}")
            return False
    return True

async def subscription_guard(update, context, next_handler):
    user = update.effective_user

    if await check_user_subscription(user.id, context):
        return await next_handler(update, context)
    else:
        channels_text = "• Канал 1\n• Канал 2\n"

        message_text = (
            "⚠️ <b>Для использования бота нужно подписаться на каналы:</b>\n\n"
            f"{channels_text}"
            "\nПосле подписки нажмите кнопку ниже."
        )

        if update.callback_query:
            await update.callback_query.edit_message_text(
                message_text,
                parse_mode="HTML",
                reply_markup=get_check_subscription_keyboard(),
            )
        else:
            await update.message.reply_text(
                message_text,
                parse_mode="HTML",
                reply_markup=get_check_subscription_keyboard(),
            )
        return None

# ================== КОМАНДЫ ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if await check_user_subscription(user.id, context):
        await update.message.reply_text(
            "👋 Добро пожаловать! Выберите нужный раздел:",
            reply_markup=main_keyboard,
        )
    else:
        await update.message.reply_text(
            "⚠️ <b>Для использования бота нужно подписаться на каналы:</b>\n\n"
            "• Канал 1\n"
            "• Канал 2\n"
            "\nПосле подписки нажмите кнопку ниже.",
            parse_mode="HTML",
            reply_markup=get_check_subscription_keyboard(),
        )

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if await check_user_subscription(user.id, context):
        await query.edit_message_text("✅ Подписка подтверждена! Добро пожаловать!")
        await context.bot.send_message(
            chat_id=user.id,
            text="Выберите нужный раздел:",
            reply_markup=main_keyboard,
        )
    else:
        await query.edit_message_text(
            "❌ Вы всё ещё не подписаны на все каналы:\n\n"
            "• Канал 1\n"
            "• Канал 2\n"
            "\nПодпишитесь и нажмите кнопку снова.",
            reply_markup=get_check_subscription_keyboard(),
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено.", reply_markup=main_keyboard)
    return ConversationHandler.END

# ================== ОБРАТНАЯ СВЯЗЬ ==================
async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def _feedback_start(update, context):
        await update.message.reply_text(
            "⚠️ <b>Эта кнопка только для срочных и важных вопросов.</b>\n\n"
            "Опишите суть дела <b>одним сообщением</b>. Флуд игнорируется.",
            parse_mode="HTML",
        )
        return FEEDBACK_TEXT

    return await subscription_guard(update, context, _feedback_start)

async def feedback_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"📩 <b>Новое сообщение обратной связи</b>\n"
            f"От: @{user.username or 'нет юзернейма'} (ID: {user.id})\n\n"
            f"{text}"
        ),
        parse_mode="HTML",
    )

    await update.message.reply_text(
        "✅ Сообщение отправлено админу. Спасибо!",
        reply_markup=main_keyboard,
    )
    return ConversationHandler.END

# ================== ПРЕДЛОЖИТЬ ПОСТ ==================
async def suggest_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def _suggest_start(update, context):
        await update.message.reply_text(
            "📝 Напишите имя модели или персонажа, чтобы админ поискал информацию и, возможно, выложил пост на канал.",
        )
        return SUGGEST_TEXT

    return await subscription_guard(update, context, _suggest_start)

async def suggest_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"💡 <b>Новое предложение поста</b>\n"
            f"От: @{user.username or 'нет юзернейма'} (ID: {user.id})\n"
            f"Модель/Персонаж: <b>{text}</b>"
        ),
        parse_mode="HTML",
    )

    await update.message.reply_text(
        "✅ Спасибо! Ваше предложение отправлено админу на рассмотрение.",
        reply_markup=main_keyboard,
    )
    return ConversationHandler.END

# ================== ДЕНЬ РОЖДЕНИЯ ==================
async def birthday_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async def _birthday_start(update, context):
        user = update.effective_user
        existing = get_user_birthday(user.id)

        if existing:
            existing_date, existing_publish, date_changed = existing
            if date_changed:
                await update.message.reply_text(
                    f"⚠️ <b>У вас уже указана дата рождения: {existing_date}</b>\n\n"
                    "Вы уже меняли дату ранее. <b>Повторная смена даты — платная.</b>\n"
                    "Для смены даты свяжитесь с админом через раздел «📞 Обратная связь».",
                    parse_mode="HTML",
                    reply_markup=main_keyboard,
                )
                return ConversationHandler.END
            else:
                await update.message.reply_text(
                    f"🎂 У вас уже указана дата рождения: <b>{existing_date}</b>\n\n"
                    "Вы можете сменить её <b>бесплатно только один раз</b>. "
                    "После этого все последующие смены будут платными.\n\n"
                    "Хотите изменить дату?",
                    parse_mode="HTML",
                    reply_markup=change_date_keyboard,
                )
                return BIRTHDAY_CHANGE_CONFIRM
        else:
            await update.message.reply_text(
                "🎂 Введите вашу дату рождения в формате <b>ДД.ММ</b>\n"
                "Например: <b>01.05</b>\n\n"
                "<i>Указывайте только день и месяц, без года.</i>",
                parse_mode="HTML",
            )
            return BIRTHDAY_DATE

    return await subscription_guard(update, context, _birthday_start)

async def birthday_change_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "change_date_cancel":
        await query.edit_message_text("✅ Дата осталась без изменений.")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Что делаем дальше?",
            reply_markup=main_keyboard,
        )
        return ConversationHandler.END

    await query.edit_message_text(
        "🎂 Введите новую дату рождения в формате <b>ДД.ММ</b>\n"
        "Например: <b>01.05</b>\n\n"
        "<i>⚠️ Это бесплатная смена. Следующая смена будет платной.</i>",
        parse_mode="HTML",
    )
    return BIRTHDAY_DATE

async def birthday_date_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    try:
        date_obj = datetime.strptime(text, "%d.%m")
        day, month = int(text.split(".")[0]), int(text.split(".")[1])
        if month < 1 or month > 12:
            raise ValueError
        if day < 1 or day > 31:
            raise ValueError
        datetime(2024, month, day)
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат или несуществующая дата.\n"
            "Пожалуйста, введите дату в формате <b>ДД.ММ</b> (например, 01.05):",
            parse_mode="HTML",
        )
        return BIRTHDAY_DATE

    context.user_data["birthday_date"] = text
    user = update.effective_user
    existing = get_user_birthday(user.id)
    is_change = existing is not None
    context.user_data["date_changed"] = 1 if is_change else 0

    await update.message.reply_text(
        "📢 Хотите ли вы, чтобы в ваш день рождения мы опубликовали ваш @username в канале "
        "для поздравлений от подписчиков?\n\n"
        "⚠️ <b>Для этого у вас должен быть установлен юзернейм в Telegram!</b>",
        parse_mode="HTML",
        reply_markup=publish_keyboard,
    )
    return BIRTHDAY_PUBLISH

async def birthday_publish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    publish = 1 if query.data == "publish_yes" else 0
    user = query.from_user
    date = context.user_data.get("birthday_date")
    date_changed = context.user_data.get("date_changed", 0)

    if publish and not user.username:
        await query.edit_message_text(
            "❌ <b>У вас не установлен юзернейм Telegram!</b>\n\n"
            "Чтобы мы могли опубликовать ваш ник в канале:\n"
            "1. Зайдите в Настройки Telegram\n"
            "2. Установите Имя пользователя (username)\n"
            "3. Вернитесь и попробуйте снова\n\n"
            "Пока что сохраняем без публикации.",
            parse_mode="HTML",
        )
        publish = 0

    save_birthday(user.id, user.username, date, publish, date_changed)

    message_text = (
        "✅ Данные сохранены!\n\n"
        "🎁 В свой день рождения вас ждёт особенный сюрприз от админа — "
        "маленький подарок придёт прямо в этот чат в течение суток. "
        "Оставайтесь на связи! ✨"
    )

    if publish:
        message_text += f"\n\n📢 Ваш ник @{user.username} будет опубликован в канале для поздравлений."

    await query.edit_message_text(message_text)

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=(
            f"🎉 <b>{'Изменён' if date_changed else 'Новый'} день рождения в базе</b>\n"
            f"Пользователь: @{user.username or 'нет юзернейма'}\n"
            f"ID: {user.id}\n"
            f"Дата: {date}\n"
            f"Публикация ника: {'Да' if publish else 'Нет'}\n"
            f"Смена даты: {'Да' if date_changed else 'Нет'}"
        ),
        parse_mode="HTML",
    )

    await context.bot.send_message(
        chat_id=user.id,
        text="Что делаем дальше?",
        reply_markup=main_keyboard,
    )
    return ConversationHandler.END

# ================== ПОВТОРНАЯ ПУБЛИКАЦИЯ ==================
async def republish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if not user.username:
        await query.edit_message_text(
            "❌ У вас всё ещё нет юзернейма.\n\n"
            "Установите его в Настройках Telegram и нажмите кнопку снова.",
            reply_markup=get_republish_keyboard(),
        )
        return

    update_username(user.id, user.username)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_1_ID,
            text=f"🎂 Сегодня день рождения у @{user.username}! Поздравляем! 🎉",
            disable_notification=True,
        )
        set_published(user.id)
        await query.edit_message_text(
            "✅ Юзернейм проверен, пост опубликован в канале! С Днём Рождения! 🎉"
        )
    except Exception as e:
        logger.error(f"Ошибка публикации в канал: {e}")
        await query.edit_message_text(
            "❌ Не удалось опубликовать пост. Админ уже уведомлён и разберётся."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Ошибка повторной публикации для @{user.username} (ID: {user.id}): {e}",
        )

# ================== ЕЖЕДНЕВНАЯ ПРОВЕРКА ==================
async def daily_birthday_check(context: ContextTypes.DEFAULT_TYPE):
    birthdays = get_today_birthdays()

    for user_id, username, publish, was_published, pending_republish in birthdays:
        if was_published:
            continue

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🎉🎂 <b>С Днём Рождения!</b> 🎂🎉\n\n"
                    "✨ Сегодня ваш особенный день, и админ не мог оставить его без внимания! ✨\n\n"
                    "🎁 <b>В течение суток вам придёт небольшой подарок</b> — "
                    "приятный сюрприз уже готовится специально для вас.\n\n"
                    "Оставайтесь на связи и пусть этот день будет наполнен теплом и радостью! 🥳💫"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить подарок {user_id}: {e}")

        if publish:
            try:
                chat = await context.bot.get_chat(user_id)
                current_username = chat.username

                if current_username:
                    await context.bot.send_message(
                        chat_id=CHANNEL_1_ID,
                        text=f"🎂 Сегодня день рождения у @{current_username}! Поздравляем! 🎉",
                        disable_notification=True,
                    )
                    update_username(user_id, current_username)
                    set_published(user_id)
                else:
                    set_pending_republish(user_id)
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⚠️ <b>Пост не был опубликован!</b>\n\n"
                            "Вы включили публикацию ника в канале, но у вас не установлен юзернейм.\n\n"
                            "Чтобы пост был опубликован:\n"
                            "1. Установите юзернейм в Настройках Telegram\n"
                            "2. Нажмите кнопку ниже"
                        ),
                        parse_mode="HTML",
                        reply_markup=get_republish_keyboard(),
                    )
            except Exception as e:
                logger.warning(f"Ошибка получения юзернейма для {user_id}: {e}")

# ================== ПРОВЕРКА ОТЛОЖЕННЫХ ПУБЛИКАЦИЙ ==================
async def check_pending_republish(context: ContextTypes.DEFAULT_TYPE):
    pending = get_pending_republish()

    for user_id, username in pending:
        try:
      