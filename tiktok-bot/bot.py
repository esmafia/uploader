"""
Telegram Bot for TikTok Auto Uploader
Uses TiktokAutoUploader (https://github.com/makiisthenes/TiktokAutoUploader)
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
UPLOADER_DIR = BASE_DIR / "uploader"
COOKIES_DIR = UPLOADER_DIR / "CookiesDir"
VIDEOS_DIR = UPLOADER_DIR / "VideosDirPath"

COOKIES_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
(UPLOADER_DIR / "output").mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────
(
    MAIN_MENU,
    ADD_ACCOUNT_WAIT_NAME,
    ADD_ACCOUNT_WAIT_COOKIE,
    UPLOAD_WAIT_ACCOUNT,
    UPLOAD_WAIT_VIDEO,
    UPLOAD_WAIT_TITLE,
    UPLOAD_WAIT_OPTIONS,
    UPLOAD_CONFIRM,
) = range(8)

# ── Helpers ────────────────────────────────────────────────────────────────

def get_saved_accounts() -> list[str]:
    accounts = []
    for f in COOKIES_DIR.glob("tiktok_session-*"):
        name = f.stem.replace("tiktok_session-", "")
        if name:
            accounts.append(name)
    return sorted(accounts)


def run_uploader(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    cmd = [sys.executable, "cli.py"] + args
    result = subprocess.run(
        cmd,
        cwd=str(UPLOADER_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Загрузить видео", callback_data="menu_upload")],
        [InlineKeyboardButton("👤 Добавить аккаунт", callback_data="menu_add_account")],
        [InlineKeyboardButton("📋 Мои аккаунты", callback_data="menu_accounts")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help")],
    ])


# ── /start → MAIN_MENU ─────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    accounts = get_saved_accounts()
    account_info = f"✅ Аккаунтов: {len(accounts)}" if accounts else "⚠️ Нет сохранённых аккаунтов"
    text = (
        f"🎵 *TikTok Auto Uploader Bot*\n\n"
        f"{account_info}\n\n"
        f"Выберите действие:"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_main_menu_keyboard())
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=_main_menu_keyboard())
    return MAIN_MENU


# ── MAIN_MENU callbacks ────────────────────────────────────────────────────

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.info("menu_callback: data=%s", data)

    if data == "menu_help":
        text = (
            "🤖 *TikTok Auto Uploader Bot*\n\n"
            "*Команды:*\n"
            "/start — главное меню\n"
            "/upload — загрузить видео\n"
            "/accounts — список аккаунтов\n"
            "/addaccount — добавить аккаунт\n"
            "/cancel — отменить операцию\n\n"
            "*Как добавить аккаунт:*\n"
            "1. Войдите в TikTok в браузере\n"
            "2. F12 → Application → Cookies → tiktok.com\n"
            "3. Скопируйте значение `sessionid`\n\n"
            "*Как загрузить видео:*\n"
            "• Отправьте mp4/mov/avi файл\n"
            "• Или YouTube-ссылку"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return MAIN_MENU

    if data == "menu_accounts":
        accounts = get_saved_accounts()
        if not accounts:
            text = "📭 Нет сохранённых аккаунтов.\n\nИспользуйте /addaccount"
        else:
            lines = "\n".join(f"• `{a}`" for a in accounts)
            text = f"👤 *Сохранённые аккаунты* ({len(accounts)}):\n\n{lines}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return MAIN_MENU

    if data == "menu_back":
        return await cmd_start(update, context)

    if data == "menu_upload":
        return await _upload_start(update, context)

    if data == "menu_add_account":
        return await _add_account_start(update, context)

    return MAIN_MENU


# ── ADD ACCOUNT flow ───────────────────────────────────────────────────────

async def _add_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (
        "👤 *Добавление аккаунта TikTok*\n\n"
        "Введите имя для аккаунта (латинские буквы, цифры, без пробелов):\n"
        "Например: `my_account`\n\n"
        "Для отмены: /cancel"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
    return ADD_ACCOUNT_WAIT_NAME


async def cmd_add_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _add_account_start(update, context)


async def add_account_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name.replace("_", "").replace("-", "").isalnum():
        await update.message.reply_text(
            "❌ Имя должно содержать только буквы, цифры, дефисы и подчёркивания.\n"
            "Попробуйте ещё раз:"
        )
        return ADD_ACCOUNT_WAIT_NAME

    context.user_data["account_name"] = name
    await update.message.reply_text(
        f"✅ Имя: `{name}`\n\n"
        "🍪 Введите значение cookie `sessionid` из TikTok.\n\n"
        "Как получить:\n"
        "1. Откройте https://www.tiktok.com\n"
        "2. Войдите в аккаунт\n"
        "3. F12 → Application → Cookies → tiktok.com\n"
        "4. Скопируйте значение `sessionid`\n\n"
        "Введите только значение cookie:",
        parse_mode="Markdown",
    )
    return ADD_ACCOUNT_WAIT_COOKIE


async def add_account_got_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    session_id = update.message.text.strip()
    name = context.user_data.get("account_name", "account")

    if len(session_id) < 20:
        await update.message.reply_text("❌ Значение cookie слишком короткое. Попробуйте ещё раз:")
        return ADD_ACCOUNT_WAIT_COOKIE

    cookie_file = COOKIES_DIR / f"tiktok_session-{name}"
    cookie_data = (
        '[{"name": "sessionid", "value": "'
        + session_id
        + '", "domain": ".tiktok.com", "path": "/", "secure": true, "httpOnly": true},'
        '{"name": "tt-target-idc", "value": "useast2a", "domain": ".tiktok.com", "path": "/", "secure": true, "httpOnly": false}]'
    )
    cookie_file.write_text(cookie_data)
    context.user_data.clear()

    await update.message.reply_text(
        f"✅ Аккаунт `{name}` сохранён!\n\n"
        "Нажмите /start для главного меню.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# ── UPLOAD flow ────────────────────────────────────────────────────────────

async def _upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    accounts = get_saved_accounts()
    if not accounts:
        text = "❌ Нет сохранённых аккаунтов.\n\nСначала добавьте аккаунт через /addaccount"
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return ConversationHandler.END

    if len(accounts) == 1:
        context.user_data["account"] = accounts[0]
        text = (
            f"✅ Аккаунт: `{accounts[0]}`\n\n"
            "📹 Отправьте видео-файл (mp4, mov, avi) или YouTube-ссылку:"
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return UPLOAD_WAIT_VIDEO

    keyboard = [[InlineKeyboardButton(a, callback_data=f"acc_{a}")] for a in accounts]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text("👤 Выберите аккаунт TikTok:", reply_markup=markup)
    else:
        await update.message.reply_text("👤 Выберите аккаунт TikTok:", reply_markup=markup)
    return UPLOAD_WAIT_ACCOUNT


async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _upload_start(update, context)


async def upload_got_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Отменено. /start — главное меню")
        return ConversationHandler.END

    account = query.data.replace("acc_", "")
    context.user_data["account"] = account
    await query.edit_message_text(
        f"✅ Аккаунт: `{account}`\n\n"
        "📹 Отправьте видео-файл (mp4, mov, avi) или YouTube-ссылку:",
        parse_mode="Markdown",
    )
    return UPLOAD_WAIT_VIDEO


async def upload_got_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    if "youtube.com" in text or "youtu.be" in text:
        context.user_data["youtube_url"] = text.strip()
        context.user_data["video_path"] = None
        await update.message.reply_text(
            f"✅ YouTube: `{text.strip()}`\n\n"
            "✏️ Введите заголовок для TikTok (до 2200 символов):",
            parse_mode="Markdown",
        )
        return UPLOAD_WAIT_TITLE
    await update.message.reply_text("❌ Пожалуйста, отправьте видео-файл или YouTube-ссылку.")
    return UPLOAD_WAIT_VIDEO


async def upload_got_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = await update.message.reply_text("⏳ Скачиваю видео...")
    video = update.message.video or update.message.document
    if not video:
        await msg.edit_text("❌ Файл не найден. Отправьте видео ещё раз.")
        return UPLOAD_WAIT_VIDEO

    file = await video.get_file()
    ext = ".mp4"
    if update.message.document and update.message.document.file_name:
        ext = Path(update.message.document.file_name).suffix or ".mp4"

    video_path = VIDEOS_DIR / f"upload_{update.message.message_id}{ext}"
    await file.download_to_drive(str(video_path))
    context.user_data["video_path"] = str(video_path)
    context.user_data["youtube_url"] = None

    await msg.edit_text(
        f"✅ Видео получено: `{video_path.name}`\n\n"
        "✏️ Введите заголовок для TikTok (до 2200 символов):",
        parse_mode="Markdown",
    )
    return UPLOAD_WAIT_TITLE


async def upload_got_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text.strip()
    if len(title) > 2200:
        await update.message.reply_text(
            f"❌ Заголовок слишком длинный ({len(title)} симв., макс. 2200).\nВведите более короткий:"
        )
        return UPLOAD_WAIT_TITLE

    context.user_data["title"] = title
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Публичное", callback_data="vis_0"),
            InlineKeyboardButton("🔒 Приватное", callback_data="vis_1"),
        ],
    ])
    await update.message.reply_text("👁 Видимость видео:", reply_markup=keyboard)
    return UPLOAD_WAIT_OPTIONS


async def upload_got_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    vis_label = "🌐 Публичное" if query.data == "vis_0" else "🔒 Приватное"
    context.user_data["visibility"] = 0 if query.data == "vis_0" else 1

    account = context.user_data.get("account")
    title = context.user_data.get("title")
    video_path = context.user_data.get("video_path")
    yt_url = context.user_data.get("youtube_url")
    source = f"`{Path(video_path).name}`" if video_path else f"`{yt_url}`"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Загрузить", callback_data="confirm_upload"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel"),
        ]
    ])
    await query.edit_message_text(
        f"📋 *Подтверждение загрузки*\n\n"
        f"👤 Аккаунт: `{account}`\n"
        f"📹 Видео: {source}\n"
        f"✏️ Заголовок: {title}\n"
        f"👁 Видимость: {vis_label}\n\n"
        "Всё верно?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return UPLOAD_CONFIRM


async def upload_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("❌ Загрузка отменена. /start — главное меню")
        _cleanup_upload(context)
        return ConversationHandler.END

    await query.edit_message_text("⏳ Загружаю видео в TikTok, подождите...")

    account = context.user_data.get("account")
    title = context.user_data.get("title")
    video_path = context.user_data.get("video_path")
    yt_url = context.user_data.get("youtube_url")
    visibility = context.user_data.get("visibility", 0)

    args = ["upload", "-u", account, "-t", title, "-vi", str(visibility)]
    if yt_url:
        args += ["-yt", yt_url]
    else:
        args += ["-v", Path(video_path).name]

    try:
        loop = asyncio.get_event_loop()
        returncode, stdout, stderr = await loop.run_in_executor(
            None, lambda: run_uploader(args, timeout=180)
        )
    except subprocess.TimeoutExpired:
        await query.message.reply_text(
            "❌ Таймаут — загрузка заняла слишком долго (>3 мин).\nПопробуйте позже."
        )
        _cleanup_upload(context)
        return ConversationHandler.END
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")
        _cleanup_upload(context)
        return ConversationHandler.END

    output = (stdout + "\n" + stderr).strip()
    log_preview = output[-1000:] if len(output) > 1000 else output

    if returncode == 0 and any(w in output.lower() for w in ("success", "uploaded", "video_id")):
        await query.message.reply_text("🎉 *Видео успешно загружено в TikTok!*", parse_mode="Markdown")
    elif returncode == 0:
        await query.message.reply_text(f"✅ Готово!\n\n```\n{log_preview}\n```", parse_mode="Markdown")
    else:
        await query.message.reply_text(
            f"❌ Ошибка загрузки (код {returncode}):\n\n```\n{log_preview}\n```",
            parse_mode="Markdown",
        )

    _cleanup_upload(context)
    return ConversationHandler.END


def _cleanup_upload(context: ContextTypes.DEFAULT_TYPE) -> None:
    video_path = context.user_data.get("video_path")
    if video_path and Path(video_path).exists():
        try:
            Path(video_path).unlink()
        except OSError:
            pass
    context.user_data.clear()


# ── /cancel ────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _cleanup_upload(context)
    if update.message:
        await update.message.reply_text("❌ Операция отменена. /start — главное меню")
    return ConversationHandler.END


# ── /accounts ──────────────────────────────────────────────────────────────

async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts = get_saved_accounts()
    if not accounts:
        text = "📭 Нет сохранённых аккаунтов.\n\nИспользуйте /addaccount"
    else:
        lines = "\n".join(f"• `{a}`" for a in accounts)
        text = f"👤 *Сохранённые аккаунты* ({len(accounts)}):\n\n{lines}"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Error handler ──────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        sys.exit(1)

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("upload", cmd_upload),
            CommandHandler("addaccount", cmd_add_account),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(menu_callback, pattern="^menu_"),
            ],
            ADD_ACCOUNT_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_got_name),
            ],
            ADD_ACCOUNT_WAIT_COOKIE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_account_got_cookie),
            ],
            UPLOAD_WAIT_ACCOUNT: [
                CallbackQueryHandler(upload_got_account, pattern="^(acc_|cancel)"),
            ],
            UPLOAD_WAIT_VIDEO: [
                MessageHandler(filters.VIDEO | filters.Document.VIDEO, upload_got_video_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, upload_got_video),
            ],
            UPLOAD_WAIT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, upload_got_title),
            ],
            UPLOAD_WAIT_OPTIONS: [
                CallbackQueryHandler(upload_got_options, pattern="^vis_"),
            ],
            UPLOAD_CONFIRM: [
                CallbackQueryHandler(upload_confirm, pattern="^(confirm_upload|cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(conv)
    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
