"""
Telegram Bot for TikTok Auto Uploader
State machine via context.user_data — no ConversationHandler.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
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

# ── States ─────────────────────────────────────────────────────────────────
S_IDLE               = None
S_ADD_ACCOUNT_NAME   = "add_account_name"
S_ADD_ACCOUNT_COOKIE = "add_account_cookie"
S_UPLOAD_VIDEO       = "upload_video"
S_UPLOAD_TITLE       = "upload_title"


def set_state(context: ContextTypes.DEFAULT_TYPE, state: str | None) -> None:
    context.user_data["state"] = state


def get_state(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get("state")


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
        cmd, cwd=str(UPLOADER_DIR), capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Загрузить видео",  callback_data="menu_upload")],
        [InlineKeyboardButton("👤 Добавить аккаунт", callback_data="menu_add_account")],
        [InlineKeyboardButton("📋 Мои аккаунты",     callback_data="menu_accounts")],
        [InlineKeyboardButton("ℹ️ Помощь",           callback_data="menu_help")],
    ])


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_IDLE)
    context.user_data.pop("account", None)
    context.user_data.pop("video_path", None)
    context.user_data.pop("youtube_url", None)
    context.user_data.pop("title", None)
    context.user_data.pop("account_name", None)

    accounts = get_saved_accounts()
    info = f"✅ Аккаунтов: {len(accounts)}" if accounts else "⚠️ Нет сохранённых аккаунтов"
    text = f"🎵 *TikTok Auto Uploader Bot*\n\n{info}\n\nВыберите действие:"

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=main_menu_markup()
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode="Markdown", reply_markup=main_menu_markup()
        )


# ── /start ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("cmd_start user=%s", update.effective_user.id)
    await send_main_menu(update, context)


# ── /cancel ────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    video_path = context.user_data.get("video_path")
    if video_path and Path(video_path).exists():
        try:
            Path(video_path).unlink()
        except OSError:
            pass
    set_state(context, S_IDLE)
    await update.message.reply_text("❌ Отменено. Нажмите /start")


# ── /accounts ──────────────────────────────────────────────────────────────

async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts = get_saved_accounts()
    if not accounts:
        text = "📭 Нет сохранённых аккаунтов.\n\nИспользуйте /addaccount"
    else:
        lines = "\n".join(f"• `{a}`" for a in accounts)
        text = f"👤 *Сохранённые аккаунты* ({len(accounts)}):\n\n{lines}"
    await update.message.reply_text(text, parse_mode="Markdown")


# ── Callback query handler (all button presses) ────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data  = query.data
    logger.info("on_callback user=%s data=%s state=%s", update.effective_user.id, data, get_state(context))
    await query.answer()

    # ── Main menu ──────────────────────────────────────────────────────────
    if data == "menu_back" or data == "menu_start":
        await send_main_menu(update, context)
        return

    if data == "menu_help":
        text = (
            "🤖 *TikTok Auto Uploader Bot*\n\n"
            "*Команды:*\n"
            "/start — главное меню\n"
            "/accounts — список аккаунтов\n"
            "/addaccount — добавить аккаунт\n"
            "/cancel — отменить операцию\n\n"
            "*Как добавить аккаунт:*\n"
            "1. Откройте https://www.tiktok.com в браузере\n"
            "2. F12 → Application → Cookies → tiktok.com\n"
            "3. Скопируйте значение `sessionid`\n\n"
            "*Как загрузить видео:*\n"
            "• Отправьте mp4/mov/avi файл\n"
            "• Или YouTube-ссылку"
        )
        back = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back)
        return

    if data == "menu_accounts":
        accounts = get_saved_accounts()
        if not accounts:
            text = "📭 Нет сохранённых аккаунтов."
        else:
            lines = "\n".join(f"• `{a}`" for a in accounts)
            text = f"👤 *Сохранённые аккаунты* ({len(accounts)}):\n\n{lines}"
        back = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back)
        return

    if data == "menu_add_account":
        set_state(context, S_ADD_ACCOUNT_NAME)
        await query.edit_message_text(
            "👤 *Добавление аккаунта TikTok*\n\n"
            "Введите имя для аккаунта (буквы, цифры, без пробелов):\n"
            "Например: `my_account`\n\n"
            "Для отмены: /cancel",
            parse_mode="Markdown",
        )
        return

    if data == "menu_upload":
        await _upload_begin(update, context)
        return

    # ── Account selection ──────────────────────────────────────────────────
    if data.startswith("acc_"):
        account = data[4:]
        context.user_data["account"] = account
        set_state(context, S_UPLOAD_VIDEO)
        await query.edit_message_text(
            f"✅ Аккаунт: `{account}`\n\n"
            "📹 Отправьте видео-файл (mp4, mov, avi) или YouTube-ссылку:",
            parse_mode="Markdown",
        )
        return

    # ── Visibility selection ───────────────────────────────────────────────
    if data in ("vis_0", "vis_1"):
        context.user_data["visibility"] = 0 if data == "vis_0" else 1
        vis_label = "🌐 Публичное" if data == "vis_0" else "🔒 Приватное"
        account   = context.user_data.get("account", "?")
        title     = context.user_data.get("title", "?")
        video_path = context.user_data.get("video_path")
        yt_url     = context.user_data.get("youtube_url")
        source     = f"`{Path(video_path).name}`" if video_path else f"`{yt_url}`"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Загрузить", callback_data="confirm_upload"),
            InlineKeyboardButton("❌ Отмена",   callback_data="cancel_upload"),
        ]])
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
        return

    # ── Confirm / cancel upload ────────────────────────────────────────────
    if data == "cancel_upload":
        _cleanup(context)
        await query.edit_message_text("❌ Загрузка отменена. /start — главное меню")
        return

    if data == "confirm_upload":
        await _do_upload(update, context)
        return

    # ── General cancel ─────────────────────────────────────────────────────
    if data == "cancel":
        _cleanup(context)
        await query.edit_message_text("❌ Отменено. /start — главное меню")
        return

    logger.warning("Unhandled callback data: %s", data)


# ── Start upload flow ──────────────────────────────────────────────────────

async def _upload_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts = get_saved_accounts()
    query = update.callback_query

    if not accounts:
        await query.edit_message_text(
            "❌ Нет сохранённых аккаунтов.\n\nСначала добавьте аккаунт через /addaccount"
        )
        return

    if len(accounts) == 1:
        context.user_data["account"] = accounts[0]
        set_state(context, S_UPLOAD_VIDEO)
        await query.edit_message_text(
            f"✅ Аккаунт: `{accounts[0]}`\n\n"
            "📹 Отправьте видео-файл (mp4, mov, avi) или YouTube-ссылку:",
            parse_mode="Markdown",
        )
        return

    keyboard = [[InlineKeyboardButton(a, callback_data=f"acc_{a}")] for a in accounts]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await query.edit_message_text(
        "👤 Выберите аккаунт TikTok:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Do the actual upload ───────────────────────────────────────────────────

async def _do_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.edit_message_text("⏳ Загружаю видео в TikTok, подождите...")

    account    = context.user_data.get("account")
    title      = context.user_data.get("title")
    video_path = context.user_data.get("video_path")
    yt_url     = context.user_data.get("youtube_url")
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
        await query.message.reply_text("❌ Таймаут (>3 мин). Попробуйте позже.")
        _cleanup(context)
        return
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")
        _cleanup(context)
        return

    output = (stdout + "\n" + stderr).strip()
    preview = output[-1000:] if len(output) > 1000 else output

    if returncode == 0 and any(w in output.lower() for w in ("success", "uploaded", "video_id")):
        await query.message.reply_text("🎉 *Видео успешно загружено в TikTok!*", parse_mode="Markdown")
    elif returncode == 0:
        await query.message.reply_text(f"✅ Готово!\n\n```\n{preview}\n```", parse_mode="Markdown")
    else:
        await query.message.reply_text(
            f"❌ Ошибка загрузки (код {returncode}):\n\n```\n{preview}\n```", parse_mode="Markdown"
        )

    _cleanup(context)


def _cleanup(context: ContextTypes.DEFAULT_TYPE) -> None:
    video_path = context.user_data.get("video_path")
    if video_path and Path(video_path).exists():
        try:
            Path(video_path).unlink()
        except OSError:
            pass
    set_state(context, S_IDLE)


# ── Text message handler (state machine) ───────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(context)
    text  = update.message.text.strip()
    logger.info("on_text user=%s state=%s", update.effective_user.id, state)

    # ── ADD ACCOUNT: waiting for name ──────────────────────────────────────
    if state == S_ADD_ACCOUNT_NAME:
        if not text.replace("_", "").replace("-", "").isalnum():
            await update.message.reply_text(
                "❌ Имя должно содержать только буквы, цифры, дефисы и подчёркивания.\n"
                "Попробуйте ещё раз:"
            )
            return
        context.user_data["account_name"] = text
        set_state(context, S_ADD_ACCOUNT_COOKIE)
        await update.message.reply_text(
            f"✅ Имя: `{text}`\n\n"
            "🍪 Введите значение cookie `sessionid` из TikTok.\n\n"
            "Как получить:\n"
            "1. Откройте https://www.tiktok.com в браузере\n"
            "2. Войдите в аккаунт\n"
            "3. F12 → Application → Cookies → tiktok.com\n"
            "4. Скопируйте значение `sessionid`\n\n"
            "Введите только значение:",
            parse_mode="Markdown",
        )
        return

    # ── ADD ACCOUNT: waiting for cookie ───────────────────────────────────
    if state == S_ADD_ACCOUNT_COOKIE:
        if len(text) < 20:
            await update.message.reply_text("❌ Значение слишком короткое. Попробуйте ещё раз:")
            return
        name = context.user_data.get("account_name", "account")
        cookie_file = COOKIES_DIR / f"tiktok_session-{name}"
        cookie_data = (
            '[{"name": "sessionid", "value": "'
            + text
            + '", "domain": ".tiktok.com", "path": "/", "secure": true, "httpOnly": true},'
            '{"name": "tt-target-idc", "value": "useast2a", "domain": ".tiktok.com", "path": "/", "secure": true, "httpOnly": false}]'
        )
        cookie_file.write_text(cookie_data)
        set_state(context, S_IDLE)
        await update.message.reply_text(
            f"✅ Аккаунт `{name}` сохранён!\n\nНажмите /start для главного меню.",
            parse_mode="Markdown",
        )
        return

    # ── UPLOAD: waiting for video / link ───────────────────────────────────
    if state == S_UPLOAD_VIDEO:
        if "youtube.com" in text or "youtu.be" in text:
            context.user_data["youtube_url"] = text
            context.user_data["video_path"]  = None
            set_state(context, S_UPLOAD_TITLE)
            await update.message.reply_text(
                f"✅ YouTube: `{text}`\n\n✏️ Введите заголовок для TikTok (до 2200 символов):",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("❌ Отправьте видео-файл или YouTube-ссылку.")
        return

    # ── UPLOAD: waiting for title ──────────────────────────────────────────
    if state == S_UPLOAD_TITLE:
        if len(text) > 2200:
            await update.message.reply_text(
                f"❌ Заголовок слишком длинный ({len(text)} симв.). Введите короче:"
            )
            return
        context.user_data["title"] = text
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Публичное", callback_data="vis_0"),
            InlineKeyboardButton("🔒 Приватное", callback_data="vis_1"),
        ]])
        await update.message.reply_text("👁 Видимость видео:", reply_markup=keyboard)
        return

    # ── Not in any flow ────────────────────────────────────────────────────
    await update.message.reply_text("Нажмите /start для главного меню.")


# ── Video file handler ─────────────────────────────────────────────────────

async def on_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if get_state(context) != S_UPLOAD_VIDEO:
        await update.message.reply_text("Нажмите /start и выберите «Загрузить видео».")
        return

    msg = await update.message.reply_text("⏳ Скачиваю видео...")
    video = update.message.video or update.message.document
    if not video:
        await msg.edit_text("❌ Файл не найден. Отправьте видео ещё раз.")
        return

    file = await video.get_file()
    ext  = ".mp4"
    if update.message.document and update.message.document.file_name:
        ext = Path(update.message.document.file_name).suffix or ".mp4"

    video_path = VIDEOS_DIR / f"upload_{update.message.message_id}{ext}"
    await file.download_to_drive(str(video_path))
    context.user_data["video_path"]  = str(video_path)
    context.user_data["youtube_url"] = None
    set_state(context, S_UPLOAD_TITLE)

    await msg.edit_text(
        f"✅ Видео получено: `{video_path.name}`\n\n✏️ Введите заголовок для TikTok:",
        parse_mode="Markdown",
    )


# ── /addaccount command ────────────────────────────────────────────────────

async def cmd_add_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_ADD_ACCOUNT_NAME)
    await update.message.reply_text(
        "👤 *Добавление аккаунта TikTok*\n\n"
        "Введите имя для аккаунта (буквы, цифры, без пробелов):\n"
        "Например: `my_account`\n\n"
        "Для отмены: /cancel",
        parse_mode="Markdown",
    )


# ── /upload command ────────────────────────────────────────────────────────

async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts = get_saved_accounts()
    if not accounts:
        await update.message.reply_text(
            "❌ Нет сохранённых аккаунтов.\n\nСначала: /addaccount"
        )
        return

    if len(accounts) == 1:
        context.user_data["account"] = accounts[0]
        set_state(context, S_UPLOAD_VIDEO)
        await update.message.reply_text(
            f"✅ Аккаунт: `{accounts[0]}`\n\n"
            "📹 Отправьте видео-файл или YouTube-ссылку:",
            parse_mode="Markdown",
        )
        return

    keyboard = [[InlineKeyboardButton(a, callback_data=f"acc_{a}")] for a in accounts]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await update.message.reply_text(
        "👤 Выберите аккаунт TikTok:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Error handler ──────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        sys.exit(1)

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    app.add_handler(CommandHandler("accounts",   cmd_accounts))
    app.add_handler(CommandHandler("addaccount", cmd_add_account))
    app.add_handler(CommandHandler("upload",     cmd_upload))

    app.add_handler(CallbackQueryHandler(on_callback))

    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, on_video_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
