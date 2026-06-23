#!/usr/bin/env python3
"""
Telegram-бот на aiogram 3.x.
- /setcookies — прислать .txt файл с куками TikTok (Netscape формат)
- /upload <url> — одно видео → GitHub Actions
- /channel <url> — все видео с канала/аккаунта (TikTok или YouTube), от старых к новым
- Просто ссылка → автоопределение: одно видео или канал
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import base64

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

BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or ""
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
        "❌ Не заданы переменные окружения:\n"
        + "\n".join(f"  • {m}" for m in MISSING)
        + "\n\nДобавьте в Replit → Secrets 🔒"
    )
    sys.exit(1)

WORKFLOW_FILE = "upload.yml"
GH_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
}

COOKIES_REPO_PATH = "tiktok-bot/cookies.json"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─── GitHub helpers ───────────────────────────────────────────────────────────

def _gh_post(path: str, body: dict, timeout: int = 15) -> tuple[bool, dict]:
    try:
        r = requests.post(f"{GH_API}/{path}", json=body, headers=GH_HEADERS, timeout=timeout)
        return r.status_code in (200, 201, 204), r.json() if r.content else {}
    except Exception as e:
        return False, {"message": str(e)}


def _gh_put(path: str, body: dict, timeout: int = 15) -> tuple[bool, dict]:
    try:
        r = requests.put(f"{GH_API}/{path}", json=body, headers=GH_HEADERS, timeout=timeout)
        return r.status_code in (200, 201), r.json() if r.content else {}
    except Exception as e:
        return False, {"message": str(e)}


def _gh_get(path: str, timeout: int = 15) -> tuple[bool, dict]:
    try:
        r = requests.get(f"{GH_API}/{path}", headers=GH_HEADERS, timeout=timeout)
        return r.status_code == 200, r.json() if r.content else {}
    except Exception as e:
        return False, {"message": str(e)}


def save_cookies_to_github(cookies_json: list) -> tuple[bool, str]:
    """Сохраняет куки в репозиторий как cookies.json (создаёт или обновляет)."""
    content_b64 = base64.b64encode(
        json.dumps(cookies_json, ensure_ascii=False, indent=2).encode()
    ).decode()

    ok, existing = _gh_get(f"contents/{COOKIES_REPO_PATH}")
    sha = existing.get("sha") if ok else None

    body = {
        "message": "update: TikTok cookies",
        "content": content_b64,
    }
    if sha:
        body["sha"] = sha

    ok, resp = _gh_put(f"contents/{COOKIES_REPO_PATH}", body)
    if ok:
        return True, f"✅ Куки сохранены ({len(cookies_json)} записей)"
    return False, f"❌ Ошибка сохранения: {resp.get('message', resp)}"


def trigger_workflow(url: str) -> tuple[bool, str]:
    """Запускает GitHub Actions workflow для одного видео."""
    ok, resp = _gh_post(
        f"actions/workflows/{WORKFLOW_FILE}/dispatches",
        {"ref": "main", "inputs": {"url": url}},
    )
    if ok:
        return True, "✅ Задача отправлена в GitHub Actions"
    return False, f"❌ GitHub API: {resp.get('message', resp)}"


def get_playlist_videos(url: str) -> list[dict]:
    """Получает список видео канала через yt-dlp (без скачивания)."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--no-warnings",
                "--playlist-reverse",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        videos = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
                video_url = info.get("url") or info.get("webpage_url") or ""
                if video_url and not video_url.startswith("http"):
                    ie_key = info.get("ie_key", "")
                    if "youtube" in ie_key.lower() or "youtube" in url.lower():
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    elif "tiktok" in ie_key.lower() or "tiktok" in url.lower():
                        video_url = f"https://www.tiktok.com/@{info.get('uploader','')}/video/{video_url}"
                if video_url.startswith("http"):
                    videos.append({
                        "url": video_url,
                        "title": info.get("title", video_url),
                    })
            except json.JSONDecodeError:
                continue
        return videos
    except subprocess.TimeoutExpired:
        return []
    except Exception as e:
        logger.error(f"yt-dlp playlist error: {e}")
        return []


def parse_netscape_cookies(text: str) -> list[dict]:
    """Парсит файл куков в Netscape-формате → список dict для Playwright."""
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path_val, secure, expiry, name, value = parts[:7]
        try:
            expiry_int = int(expiry)
        except ValueError:
            expiry_int = -1
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip(".") if not domain.startswith(".") else domain,
            "path": path_val,
            "expires": expiry_int,
            "httpOnly": False,
            "secure": secure.upper() == "TRUE",
            "sameSite": "None",
        })
    return cookies


# ─── Handlers ─────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_start(message: Message):
    await message.answer(
        "🤖 *TikTok Uploader Bot*\n\n"
        "📋 *Команды:*\n"
        "`/setcookies` — как получить и отправить куки TikTok\n"
        "`/upload <url>` — загрузить одно видео\n"
        "`/channel <url>` — все видео с канала/аккаунта (от старых к новым)\n\n"
        "Или просто отправьте ссылку — бот сам определит что это.\n\n"
        "📊 Статус задач: "
        f"[GitHub Actions](https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions)",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


@dp.message(Command("setcookies"))
async def cmd_setcookies(message: Message):
    await message.answer(
        "🍪 *Как получить cookies TikTok:*\n\n"
        "1. Войдите в TikTok в браузере Chrome\n"
        "2. Установите расширение *Get cookies.txt LOCALLY*\n"
        "   (ищите в Chrome Web Store)\n"
        "3. Откройте `tiktok.com`\n"
        "4. Нажмите на расширение → *Export As* → *Netscape*\n"
        "5. Отправьте скачанный `.txt` файл *сюда* в чат 📎\n\n"
        "⚡ Бот сохранит куки в репозиторий — они будут использоваться для всех "
        "следующих загрузок.\n"
        "Для смены аккаунта — просто отправьте новый файл.",
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
    await _dispatch_single(message, url)


@dp.message(Command("channel"))
async def cmd_channel(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "❗ Укажите ссылку на канал:\n"
            "`/channel https://www.youtube.com/@ChannelName`\n"
            "`/channel https://www.tiktok.com/@username`",
            parse_mode="Markdown",
        )
        return
    url = parts[1].strip().strip("<>")
    await _dispatch_channel(message, url)


@dp.message(F.text.regexp(r"https?://"))
async def handle_url(message: Message):
    url = (message.text or "").strip().strip("<>")
    channel_keywords = ["/@", "/channel/", "/c/", "/user/", "/playlist?"]
    if any(k in url for k in channel_keywords):
        await _dispatch_channel(message, url)
    else:
        await _dispatch_single(message, url)


@dp.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    fname = doc.file_name or ""
    if not fname.lower().endswith(".txt"):
        return

    msg = await message.answer("⏳ Читаю файл куков...")
    try:
        file_obj = await doc.get_file()
        raw = await file_obj.download_as_bytearray()
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        await msg.edit_text(f"❌ Не удалось прочитать файл: {e}")
        return

    if "tiktok.com" not in text.lower() and "# Netscape" not in text:
        await msg.edit_text(
            "❌ Файл не похож на cookies TikTok.\n"
            "Используйте расширение *Get cookies.txt LOCALLY* и экспортируйте в Netscape-формате.",
            parse_mode="Markdown",
        )
        return

    await msg.edit_text("⏳ Парсю и сохраняю куки в репозиторий...")
    cookies = parse_netscape_cookies(text)
    if not cookies:
        await msg.edit_text("❌ Не нашёл куки в файле. Убедитесь что это Netscape-формат.")
        return

    ok, result_msg = await asyncio.to_thread(save_cookies_to_github, cookies)
    await msg.edit_text(
        f"{result_msg}\n\n"
        "Теперь отправьте ссылку на видео или канал.",
        parse_mode="Markdown",
    )


# ─── Dispatch helpers ─────────────────────────────────────────────────────────

async def _dispatch_single(message: Message, url: str):
    msg = await message.answer(
        f"⏳ Отправляю задачу в GitHub Actions...\n`{url}`",
        parse_mode="Markdown",
    )
    ok, text = await asyncio.to_thread(trigger_workflow, url)
    await msg.edit_text(
        f"{text}\n\n"
        f"📊 [Следить за прогрессом](https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions)",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def _dispatch_channel(message: Message, url: str):
    msg = await message.answer(
        f"🔍 Получаю список видео...\n`{url}`\n\n"
        "_Это займёт до 30 секунд..._",
        parse_mode="Markdown",
    )

    videos = await asyncio.to_thread(get_playlist_videos, url)

    if not videos:
        await msg.edit_text(
            "❌ Не удалось получить список видео.\n\n"
            "Возможные причины:\n"
            "• Приватный канал\n"
            "• Неверная ссылка\n"
            "• Канал не поддерживается\n\n"
            "Попробуйте `/upload <ссылка_на_конкретное_видео>`",
            parse_mode="Markdown",
        )
        return

    await msg.edit_text(
        f"📋 Найдено *{len(videos)}* видео (порядок: от старых к новым)\n"
        f"⏳ Отправляю задачи в GitHub Actions...",
        parse_mode="Markdown",
    )

    sent = 0
    failed = 0
    for i, video in enumerate(videos, 1):
        ok, _ = await asyncio.to_thread(trigger_workflow, video["url"])
        if ok:
            sent += 1
        else:
            failed += 1
        if i % 5 == 0 or i == len(videos):
            await msg.edit_text(
                f"📤 Отправляю задачи: {i}/{len(videos)}...",
                parse_mode="Markdown",
            )
        await asyncio.sleep(0.3)

    status = "✅" if failed == 0 else "⚠️"
    await msg.edit_text(
        f"{status} *Готово!*\n\n"
        f"✅ Отправлено: {sent} задач\n"
        f"{'❌ Ошибок: ' + str(failed) + chr(10) if failed else ''}"
        f"\n📊 [Следить за прогрессом](https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions)",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

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
