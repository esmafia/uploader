#!/usr/bin/env python3
"""
Telegram-бот на aiogram 3.x.
Принимает ссылку на видео и запускает GitHub Actions Workflow для загрузки в TikTok.
Playwright/Chromium НЕ запускается в Replit — только в GitHub Actions.
"""
import asyncio
import logging
import os
import sys

import requests
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from keep_alive import keep_alive

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BOT_TOKEN = (
    os.environ.get("BOT_TOKEN")
    or os.environ.get("TELEGRAM_TOKEN")
    or ""
)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

MISSING = []
if not BOT_TOKEN:
    MISSING.append("BOT_TOKEN (или TELEGRAM_TOKEN)")
if not GITHUB_TOKEN:
    MISSING.append("GITHUB_TOKEN")
if not GITHUB_OWNER:
    MISSING.append("GITHUB_OWNER")
if not GITHUB_REPO:
    MISSING.append("GITHUB_REPO")

if MISSING:
    logger.error(
        f"❌ Не заданы обязательные переменные окружения:\n"
        + "\n".join(f"  • {m}" for m in MISSING)
        + "\n\nДобавьте их в Replit → Secrets (вкладка с замком 🔒)."
    )
    sys.exit(1)

WORKFLOW_FILE = "upload.yml"
GITHUB_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
    f"/actions/workflows/{WORKFLOW_FILE}/dispatches"
)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def trigger_github_workflow(url: str) -> tuple[bool, str]:
    """Отправляет POST-запрос в GitHub API для запуска workflow."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "ref": "main",
        "inputs": {
            "url": url
        },
    }
    try:
        resp = requests.post(GITHUB_API_URL, json=payload, headers=headers, timeout=15)
        if resp.status_code == 204:
            return True, "✅ Задача отправлена в GitHub Actions!"
        else:
            return False, (
                f"❌ GitHub API вернул ошибку {resp.status_code}:\n"
                f"`{resp.text[:400]}`"
            )
    except requests.exceptions.Timeout:
        return False, "❌ GitHub API не ответил за 15 секунд. Попробуйте ещё раз."
    except requests.exceptions.RequestException as exc:
        return False, f"❌ Ошибка сети при обращении к GitHub: {exc}"


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "🤖 *TikTok Uploader Bot*\n\n"
        "Отправьте ссылку на видео — бот запустит загрузку через GitHub Actions.\n\n"
        "📋 Команды:\n"
        "`/upload <url>` — загрузить видео по ссылке\n"
        "`/status` — как проверить статус задачи\n"
        "`/help` — справка\n\n"
        "Поддерживается YouTube, TikTok и другие сайты из yt-dlp.",
        parse_mode="Markdown",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📋 *Справка*\n\n"
        "1. Отправьте `/upload <url>` или просто вставьте ссылку на видео.\n"
        "2. Бот запустит задачу в GitHub Actions.\n"
        "3. GitHub Actions скачает видео и загрузит его в TikTok.\n\n"
        "⚙️ Статус задачи можно посмотреть на вкладке *Actions* вашего репозитория:\n"
        f"`https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions`",
        parse_mode="Markdown",
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions"
    await message.answer(
        f"📊 Статус задач GitHub Actions:\n{url}",
        parse_mode="Markdown",
    )


@dp.message(Command("upload"))
async def cmd_upload(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "❗ Укажите ссылку:\n`/upload https://youtube.com/watch?v=...`",
            parse_mode="Markdown",
        )
        return

    url = parts[1].strip().strip("<>")
    await _dispatch(message, url)


@dp.message(F.text.startswith("http"))
async def handle_url(message: Message):
    url = (message.text or "").strip().strip("<>")
    await _dispatch(message, url)


async def _dispatch(message: Message, url: str):
    """Общая логика: уведомляем пользователя и диспетчеризируем в GitHub Actions."""
    msg = await message.answer(
        f"⏳ Отправляю задачу в GitHub Actions...\n`{url}`",
        parse_mode="Markdown",
    )

    ok, text = await asyncio.to_thread(trigger_github_workflow, url)

    try:
        await msg.edit_text(
            f"{text}\n\n"
            f"📊 Следите за прогрессом:\n"
            f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions",
            parse_mode="Markdown",
        )
    except Exception:
        await message.answer(text, parse_mode="Markdown")


async def main():
    logger.info(
        f"🚀 Запуск бота...\n"
        f"   GitHub: {GITHUB_OWNER}/{GITHUB_REPO}\n"
        f"   Workflow: {WORKFLOW_FILE}"
    )
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    keep_alive()
    asyncio.run(main())
