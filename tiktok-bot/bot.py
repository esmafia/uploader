"""
Telegram Bot for TikTok Auto Uploader
State machine via context.user_data — no ConversationHandler.

Improvements:
- Video upload streams from disk (no full-file RAM buffer)
- Upload runs in background thread — bot never freezes
- Delete account support
- Bulk import: give a TikTok/YouTube channel URL, bot downloads oldest→newest
  and publishes each video without stopping
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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
BASE_DIR    = Path(__file__).parent
UPLOADER_DIR = BASE_DIR / "uploader"
COOKIES_DIR  = UPLOADER_DIR / "CookiesDir"
VIDEOS_DIR   = UPLOADER_DIR / "VideosDirPath"

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
S_BULK_ACCOUNT       = "bulk_account"      # waiting for account selection for bulk import
S_BULK_URL           = "bulk_url"          # waiting for channel URL

# ── Background bulk-import queue ──────────────────────────────────────────
# Each entry: {"chat_id", "account", "url", "visibility"}
@dataclass
class BulkJob:
    chat_id: int
    account: str
    url: str
    visibility: int
    status: str = "pending"       # pending | running | done | error | cancelled
    current: int = 0
    total: int = 0
    cancel_flag: threading.Event = field(default_factory=threading.Event)

# Global: chat_id -> BulkJob (one job per chat at a time)
_bulk_jobs: dict[int, BulkJob] = {}
_bulk_lock  = threading.Lock()


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


def delete_account(name: str) -> bool:
    cookie_file = COOKIES_DIR / f"tiktok_session-{name}"
    if cookie_file.exists():
        cookie_file.unlink()
        return True
    return False


def run_uploader(args: list[str], timeout: int = 600) -> tuple[int, str, str]:
    cmd = [sys.executable, "cli.py"] + args
    result = subprocess.run(
        cmd, cwd=str(UPLOADER_DIR), capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Загрузить видео",       callback_data="menu_upload")],
        [InlineKeyboardButton("🔗 Импорт из аккаунта",   callback_data="menu_bulk")],
        [InlineKeyboardButton("👤 Добавить аккаунт",      callback_data="menu_add_account")],
        [InlineKeyboardButton("📋 Мои аккаунты",          callback_data="menu_accounts")],
        [InlineKeyboardButton("ℹ️ Помощь",                callback_data="menu_help")],
    ])


async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_IDLE)
    for key in ("account", "video_path", "youtube_url", "title", "account_name",
                "bulk_url", "bulk_visibility"):
        context.user_data.pop(key, None)

    accounts = get_saved_accounts()
    info = f"✅ Аккаунтов: {len(accounts)}" if accounts else "⚠️ Нет сохранённых аккаунтов"

    # Show bulk-import status if running
    chat_id = update.effective_chat.id
    with _bulk_lock:
        job = _bulk_jobs.get(chat_id)
    bulk_line = ""
    if job and job.status == "running":
        bulk_line = f"\n\n🔄 *Импорт:* видео {job.current}/{job.total or '?'}"
    elif job and job.status == "done":
        bulk_line = f"\n\n✅ *Импорт завершён:* {job.current} видео загружено"

    text = f"🎵 *TikTok Auto Uploader Bot*\n\n{info}{bulk_line}\n\nВыберите действие:"

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


# ── Callback query handler (all button presses) ─────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query   = update.callback_query
    data    = query.data
    chat_id = update.effective_chat.id
    logger.info("on_callback user=%s data=%s state=%s", update.effective_user.id, data, get_state(context))
    await query.answer()

    # ── Main menu ──────────────────────────────────────────────────────────
    if data in ("menu_back", "menu_start"):
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
            "• Или YouTube-ссылку\n\n"
            "*Импорт из аккаунта:*\n"
            "• Дайте ссылку на YouTube/TikTok-канал\n"
            "• Бот скачает все видео от старых к новым\n"
            "• И опубликует их в TikTok без остановки"
        )
        back = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back)
        return

    # ── Accounts list with delete buttons ─────────────────────────────────
    if data == "menu_accounts":
        accounts = get_saved_accounts()
        if not accounts:
            text = "📭 Нет сохранённых аккаунтов."
            kb = [[InlineKeyboardButton("◀️ Назад", callback_data="menu_back")]]
        else:
            text = "👤 *Сохранённые аккаунты* — нажмите 🗑 для удаления:"
            kb = []
            for a in accounts:
                kb.append([
                    InlineKeyboardButton(f"👤 {a}", callback_data=f"acc_info_{a}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"del_acc_{a}"),
                ])
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_back")])
        await query.edit_message_text(text, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(kb))
        return

    # ── Delete account ─────────────────────────────────────────────────────
    if data.startswith("del_acc_"):
        name = data[8:]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"del_acc_confirm_{name}"),
            InlineKeyboardButton("❌ Отмена",       callback_data="menu_accounts"),
        ]])
        await query.edit_message_text(
            f"⚠️ Удалить аккаунт `{name}`?\n\nЭто удалит cookie-файл и сессию.",
            parse_mode="Markdown", reply_markup=kb,
        )
        return

    if data.startswith("del_acc_confirm_"):
        name = data[16:]
        ok = delete_account(name)
        msg = f"✅ Аккаунт `{name}` удалён." if ok else f"❌ Аккаунт `{name}` не найден."
        accounts = get_saved_accounts()
        if not accounts:
            kb_rows = []
        else:
            kb_rows = []
            for a in accounts:
                kb_rows.append([
                    InlineKeyboardButton(f"👤 {a}", callback_data=f"acc_info_{a}"),
                    InlineKeyboardButton("🗑 Удалить", callback_data=f"del_acc_{a}"),
                ])
        kb_rows.append([InlineKeyboardButton("◀️ Назад", callback_data="menu_back")])
        await query.edit_message_text(
            msg + ("\n\n👤 *Аккаунты:*" if accounts else "\n\n📭 Нет аккаунтов."),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    # ── Add account ────────────────────────────────────────────────────────
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

    # ── Upload ─────────────────────────────────────────────────────────────
    if data == "menu_upload":
        await _upload_begin(update, context)
        return

    # ── Bulk import from channel ───────────────────────────────────────────
    if data == "menu_bulk":
        await _bulk_begin(update, context)
        return

    # ── Account selection (for upload) ─────────────────────────────────────
    if data.startswith("acc_") and not data.startswith("acc_info_"):
        account = data[4:]
        context.user_data["account"] = account
        set_state(context, S_UPLOAD_VIDEO)
        await query.edit_message_text(
            f"✅ Аккаунт: `{account}`\n\n"
            "📹 Отправьте видео-файл (mp4, mov, avi) или YouTube-ссылку:",
            parse_mode="Markdown",
        )
        return

    # ── Account selection (for bulk import) ───────────────────────────────
    if data.startswith("bulk_acc_"):
        account = data[9:]
        context.user_data["account"] = account
        set_state(context, S_BULK_URL)
        await query.edit_message_text(
            f"✅ Аккаунт: `{account}`\n\n"
            "🔗 Отправьте ссылку на YouTube или TikTok канал/профиль:\n\n"
            "Примеры:\n"
            "• `https://www.youtube.com/@channel`\n"
            "• `https://www.tiktok.com/@username`\n\n"
            "Бот скачает все видео от старых к новым и загрузит их в TikTok.",
            parse_mode="Markdown",
        )
        return

    # ── Visibility selection (upload) ──────────────────────────────────────
    if data in ("vis_0", "vis_1"):
        context.user_data["visibility"] = 0 if data == "vis_0" else 1
        vis_label  = "🌐 Публичное" if data == "vis_0" else "🔒 Приватное"
        account    = context.user_data.get("account", "?")
        title      = context.user_data.get("title", "?")
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

    # ── Visibility selection (bulk import) ────────────────────────────────
    if data in ("bulk_vis_0", "bulk_vis_1"):
        visibility = 0 if data == "bulk_vis_0" else 1
        context.user_data["bulk_visibility"] = visibility
        account = context.user_data.get("account", "?")
        url     = context.user_data.get("bulk_url", "?")
        vis_label = "🌐 Публичное" if visibility == 0 else "🔒 Приватное"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️ Начать импорт", callback_data="bulk_confirm"),
            InlineKeyboardButton("❌ Отмена",         callback_data="cancel"),
        ]])
        await query.edit_message_text(
            f"📋 *Импорт из аккаунта*\n\n"
            f"👤 TikTok аккаунт: `{account}`\n"
            f"🔗 Источник: `{url}`\n"
            f"👁 Видимость: {vis_label}\n\n"
            "Начать? Бот будет загружать видео в фоне.",
            parse_mode="Markdown",
            reply_markup=kb,
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

    # ── Confirm bulk import ───────────────────────────────────────────────
    if data == "bulk_confirm":
        await _start_bulk_import(update, context)
        return

    # ── Cancel bulk import ────────────────────────────────────────────────
    if data == "bulk_cancel":
        with _bulk_lock:
            job = _bulk_jobs.get(chat_id)
        if job:
            job.cancel_flag.set()
            job.status = "cancelled"
        await query.edit_message_text("🛑 Импорт остановлен. /start — главное меню")
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
    query    = update.callback_query

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


# ── Start bulk import flow ─────────────────────────────────────────────────

async def _bulk_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts = get_saved_accounts()
    query    = update.callback_query
    chat_id  = update.effective_chat.id

    # Check if there's already a running job
    with _bulk_lock:
        job = _bulk_jobs.get(chat_id)
    if job and job.status == "running":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 Остановить", callback_data="bulk_cancel"),
            InlineKeyboardButton("◀️ Назад",      callback_data="menu_back"),
        ]])
        await query.edit_message_text(
            f"🔄 *Импорт уже идёт*\n\n"
            f"👤 {job.account}\n"
            f"🔗 {job.url}\n"
            f"📊 Загружено: {job.current}/{job.total or '?'}",
            parse_mode="Markdown", reply_markup=kb,
        )
        return

    if not accounts:
        await query.edit_message_text(
            "❌ Нет сохранённых аккаунтов.\n\nСначала добавьте аккаунт через /addaccount"
        )
        return

    if len(accounts) == 1:
        context.user_data["account"] = accounts[0]
        set_state(context, S_BULK_URL)
        await query.edit_message_text(
            f"✅ Аккаунт: `{accounts[0]}`\n\n"
            "🔗 Отправьте ссылку на YouTube или TikTok канал/профиль:\n\n"
            "Примеры:\n"
            "• `https://www.youtube.com/@channel`\n"
            "• `https://www.tiktok.com/@username`\n\n"
            "Бот скачает все видео от старых к новым и загрузит их в TikTok.",
            parse_mode="Markdown",
        )
        return

    keyboard = [[InlineKeyboardButton(a, callback_data=f"bulk_acc_{a}")] for a in accounts]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    await query.edit_message_text(
        "👤 В какой TikTok аккаунт загружать?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── Start the actual bulk import background task ───────────────────────────

def _try_dispatch_github(account: str, url: str, visibility: int, chat_id: int) -> bool:
    """
    Dispatch a GitHub Actions workflow_dispatch event.
    Returns True if the request was accepted (HTTP 204), False otherwise.
    Requires env vars GITHUB_TOKEN and GITHUB_REPO.
    """
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_repo  = os.environ.get("GITHUB_REPO", "").strip()
    if not github_token or not github_repo:
        return False

    cookie_file = COOKIES_DIR / f"tiktok_session-{account}"
    if not cookie_file.exists():
        logger.error("Cookie file not found for account %s", account)
        return False

    cookie_b64 = base64.b64encode(cookie_file.read_bytes()).decode()

    payload = json.dumps({
        "ref": os.environ.get("GITHUB_BRANCH", "main"),
        "inputs": {
            "account":    account,
            "source_url": url,
            "visibility": str(visibility),
            "cookie_b64": cookie_b64,
            "chat_id":    str(chat_id),
        },
    }).encode()

    api_url = (
        f"https://api.github.com/repos/{github_repo}"
        f"/actions/workflows/tiktok_bulk_upload.yml/dispatches"
    )
    req = urllib.request.Request(
        api_url, data=payload,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept":        "application/vnd.github+json",
            "Content-Type":  "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 204
    except urllib.error.HTTPError as e:
        logger.error("GitHub dispatch HTTP error: %s %s", e.code, e.read()[:300])
        return False
    except Exception as e:
        logger.error("GitHub dispatch error: %s", e)
        return False


async def _start_bulk_import(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query      = update.callback_query
    chat_id    = update.effective_chat.id
    account    = context.user_data.get("account")
    url        = context.user_data.get("bulk_url")
    visibility = context.user_data.get("bulk_visibility", 0)

    # ── Try GitHub Actions first (avoids Replit dying under heavy load) ──────
    dispatched = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _try_dispatch_github(account, url, visibility, chat_id)
    )

    if dispatched:
        await query.edit_message_text(
            "☁️ *Импорт отправлен на GitHub Actions!*\n\n"
            f"👤 Аккаунт: `{account}`\n"
            f"🔗 Источник: `{url}`\n\n"
            "Бот будет присылать обновления по ходу загрузки.\n"
            "Replit при этом не нагружается.",
            parse_mode="Markdown",
        )
        set_state(context, S_IDLE)
        for key in ("account", "bulk_url", "bulk_visibility"):
            context.user_data.pop(key, None)
        return

    # ── Fallback: run locally in a background thread ─────────────────────────
    job = BulkJob(chat_id=chat_id, account=account, url=url, visibility=visibility)
    with _bulk_lock:
        _bulk_jobs[chat_id] = job

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 Остановить", callback_data="bulk_cancel"),
    ]])
    await query.edit_message_text(
        "🔄 *Импорт запущен в фоне!*\n\n"
        f"👤 Аккаунт: `{account}`\n"
        f"🔗 Источник: `{url}`\n\n"
        "Бот будет отправлять обновления по ходу загрузки.\n"
        "Вы можете продолжать пользоваться ботом.",
        parse_mode="Markdown",
        reply_markup=kb,
    )

    # Launch background thread — bot stays responsive
    app = context.application
    loop = asyncio.get_event_loop()
    threading.Thread(
        target=_bulk_worker,
        args=(job, app, loop),
        daemon=True,
    ).start()

    set_state(context, S_IDLE)
    for key in ("account", "bulk_url", "bulk_visibility"):
        context.user_data.pop(key, None)


def _bulk_worker(job: BulkJob, app: Application, loop: asyncio.AbstractEventLoop) -> None:
    """
    Runs in a background thread.
    1. Uses yt-dlp to get the full video list (oldest first).
    2. Downloads each video one at a time.
    3. Uploads it to TikTok via cli.py.
    4. Deletes the local file immediately to free disk space.
    """
    def send(text: str) -> None:
        asyncio.run_coroutine_threadsafe(
            app.bot.send_message(chat_id=job.chat_id, text=text, parse_mode="Markdown"),
            loop,
        ).result(timeout=30)

    try:
        job.status = "running"

        # ── Step 1: Get video list with descriptions (oldest first) ──────────
        send(f"📋 Получаю список видео из `{job.url}`…")

        ydl_list_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--skip-download",
            "--print", "%(id)s\t%(title)s\t%(description)s\t%(url)s",
            "--playlist-reverse",   # oldest first
            "--no-warnings",
            "--quiet",
            job.url,
        ]
        proc = subprocess.run(
            ydl_list_cmd,
            capture_output=True, text=True, timeout=180,
            cwd=str(UPLOADER_DIR),
        )
        if proc.returncode != 0 and not proc.stdout.strip():
            send(f"❌ Не удалось получить список видео:\n```\n{proc.stderr[-500:]}\n```")
            job.status = "error"
            return

        lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
        if not lines:
            send("❌ Плейлист пуст или ссылка не поддерживается.")
            job.status = "error"
            return

        job.total = len(lines)
        send(f"✅ Найдено видео: *{job.total}*\nНачинаю загрузку от самых старых…")

        # ── Step 2: Process each video ────────────────────────────────────
        for idx, line in enumerate(lines, 1):
            if job.cancel_flag.is_set():
                send(f"🛑 Импорт остановлен на видео {idx}/{job.total}.")
                job.status = "cancelled"
                return

            parts = line.split("\t", 3)
            vid_title = parts[1].strip() if len(parts) > 1 else f"video_{idx}"
            vid_desc  = parts[2].strip() if len(parts) > 2 else ""
            vid_url   = parts[3].strip() if len(parts) > 3 else job.url

            # Use original description as caption; fall back to title.
            # TikTok caption limit: 2200 chars.
            caption = (vid_desc if vid_desc else vid_title)[:2200].strip() or f"video_{idx}"

            send(f"📥 *{idx}/{job.total}* Скачиваю:\n_{vid_title[:120]}_")

            out_file = VIDEOS_DIR / f"bulk_{job.chat_id}_{idx}.mp4"

            # Download video to disk
            ydl_dl_cmd = [
                sys.executable, "-m", "yt_dlp",
                "--no-playlist",
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", str(out_file),
                "--no-warnings",
                "--quiet",
                vid_url,
            ]
            dl_proc = subprocess.run(
                ydl_dl_cmd,
                capture_output=True, text=True, timeout=300,
                cwd=str(UPLOADER_DIR),
            )
            if dl_proc.returncode != 0 or not out_file.exists():
                send(f"⚠️ *{idx}/{job.total}* Не удалось скачать, пропускаю.\n```\n{dl_proc.stderr[-300:]}\n```")
                job.current = idx
                continue

            if job.cancel_flag.is_set():
                out_file.unlink(missing_ok=True)
                send(f"🛑 Импорт остановлен после скачивания {idx}/{job.total}.")
                job.status = "cancelled"
                return

            send(f"📤 *{idx}/{job.total}* Публикую в TikTok…")

            # Upload via cli.py
            try:
                rc, stdout, stderr = _run_upload_with_file(
                    account=job.account,
                    video_path=str(out_file),
                    title=caption,
                    visibility=job.visibility,
                )
            except subprocess.TimeoutExpired:
                send(f"⚠️ *{idx}/{job.total}* Таймаут загрузки, пропускаю.")
                out_file.unlink(missing_ok=True)
                job.current = idx
                continue
            finally:
                # Always delete the local file to free disk
                out_file.unlink(missing_ok=True)

            if rc == 0:
                send(f"✅ *{idx}/{job.total}* Опубликовано: _{vid_title[:100]}_")
            else:
                output = (stdout + "\n" + stderr).strip()
                send(f"⚠️ *{idx}/{job.total}* Ошибка публикации:\n```\n{output[-300:]}\n```")

            job.current = idx

        send(f"🎉 *Импорт завершён!*\nВсего загружено: {job.total} видео из `{job.url}`.")
        job.status = "done"

    except Exception as e:
        logger.exception("Bulk worker error: %s", e)
        try:
            send(f"❌ Критическая ошибка импорта: {e}")
        except Exception:
            pass
        job.status = "error"


def _run_upload_with_file(account: str, video_path: str, title: str,
                           visibility: int, timeout: int = 600) -> tuple[int, str, str]:
    """Upload a local file to TikTok using cli.py with an absolute path."""
    cmd = [
        sys.executable, "cli.py", "upload",
        "-u", account,
        "-v", video_path,
        "-t", title,
        "-vi", str(visibility),
    ]
    result = subprocess.run(
        cmd, cwd=str(UPLOADER_DIR), capture_output=True, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


# ── Do the actual single-video upload ─────────────────────────────────────

async def _do_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.edit_message_text("⏳ Загружаю видео в TikTok (в фоне)…\nБот не заморозится.")

    account    = context.user_data.get("account")
    title      = context.user_data.get("title")
    video_path = context.user_data.get("video_path")
    yt_url     = context.user_data.get("youtube_url")
    visibility = context.user_data.get("visibility", 0)

    args = ["upload", "-u", account, "-t", title, "-vi", str(visibility)]
    if yt_url:
        args += ["-yt", yt_url]
    else:
        args += ["-v", video_path]

    # Run in executor so the event loop (and bot) stay responsive
    loop = asyncio.get_event_loop()
    try:
        returncode, stdout, stderr = await loop.run_in_executor(
            None, lambda: run_uploader(args, timeout=600)
        )
    except subprocess.TimeoutExpired:
        await query.message.reply_text("❌ Таймаут (>10 мин). Попробуйте позже.")
        _cleanup(context)
        return
    except Exception as e:
        await query.message.reply_text(f"❌ Ошибка: {e}")
        _cleanup(context)
        return

    output  = (stdout + "\n" + stderr).strip()
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


# ── Text message handler (state machine) ─────────────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(context)
    text  = update.message.text.strip()
    logger.info("on_text user=%s state=%s", update.effective_user.id, state)

    # ── ADD ACCOUNT: waiting for name ─────────────────────────────────────
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

    # ── ADD ACCOUNT: waiting for cookie ──────────────────────────────────
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

    # ── UPLOAD: waiting for video / link ──────────────────────────────────
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

    # ── UPLOAD: waiting for title ─────────────────────────────────────────
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

    # ── BULK: waiting for channel URL ─────────────────────────────────────
    if state == S_BULK_URL:
        if not (text.startswith("http://") or text.startswith("https://")):
            await update.message.reply_text(
                "❌ Введите полную ссылку (начиная с https://).\n\n"
                "Например: `https://www.youtube.com/@channel`",
                parse_mode="Markdown",
            )
            return
        context.user_data["bulk_url"] = text
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌐 Публичное", callback_data="bulk_vis_0"),
            InlineKeyboardButton("🔒 Приватное", callback_data="bulk_vis_1"),
        ]])
        await update.message.reply_text(
            f"✅ Ссылка: `{text}`\n\n👁 Видимость для всех загружаемых видео:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return

    # ── Not in any flow ────────────────────────────────────────────────────
    await update.message.reply_text("Нажмите /start для главного меню.")


# ── Video file handler ────────────────────────────────────────────────────

async def on_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if get_state(context) != S_UPLOAD_VIDEO:
        await update.message.reply_text("Нажмите /start и выберите «Загрузить видео».")
        return

    msg   = await update.message.reply_text("⏳ Скачиваю видео...")
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


# ── /addaccount command ───────────────────────────────────────────────────

async def cmd_add_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_ADD_ACCOUNT_NAME)
    await update.message.reply_text(
        "👤 *Добавление аккаунта TikTok*\n\n"
        "Введите имя для аккаунта (буквы, цифры, без пробелов):\n"
        "Например: `my_account`\n\n"
        "Для отмены: /cancel",
        parse_mode="Markdown",
    )


# ── /upload command ───────────────────────────────────────────────────────

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


# ── Error handler ─────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception:", exc_info=context.error)


# ── Main ──────────────────────────────────────────────────────────────────

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
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "inline_query"],
    )


if __name__ == "__main__":
    main()
