#!/usr/bin/env python3
"""
Standalone bulk-upload runner for GitHub Actions.

Required env vars:
  ACCOUNT        — TikTok account name (matches cookie filename)
  SOURCE_URL     — YouTube or TikTok channel/profile URL
  VISIBILITY     — 0 = public, 1 = private (default 0)
  COOKIE_B64     — base64-encoded contents of the tiktok_session-<name> cookie file
  CHAT_ID        — Telegram chat ID to send progress updates to
  TELEGRAM_TOKEN — Telegram bot token

Optional:
  CHROME_BIN         — path to chrome/chromium binary
  CHROMEDRIVER_PATH  — path to chromedriver binary
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR   = Path(__file__).parent
UPLOADER_DIR = SCRIPT_DIR / "uploader"
COOKIES_DIR  = UPLOADER_DIR / "CookiesDir"
VIDEOS_DIR   = UPLOADER_DIR / "VideosDirPath"

COOKIES_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
(UPLOADER_DIR / "output").mkdir(parents=True, exist_ok=True)

ACCOUNT        = os.environ["ACCOUNT"]
SOURCE_URL     = os.environ["SOURCE_URL"]
VISIBILITY     = int(os.environ.get("VISIBILITY", "0"))
COOKIE_B64     = os.environ["COOKIE_B64"]
CHAT_ID        = os.environ["CHAT_ID"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]


def send_telegram(text: str) -> None:
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except Exception as e:
        print(f"[Telegram send error] {e}", file=sys.stderr)


def run_ydlp(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp"] + args,
        capture_output=True, text=True, timeout=timeout,
        cwd=str(UPLOADER_DIR),
    )


def run_upload(video_path: str, title: str) -> tuple[int, str]:
    cmd = [
        sys.executable, "cli.py", "upload",
        "-u", ACCOUNT,
        "-v", video_path,
        "-t", title,
        "-vi", str(VISIBILITY),
    ]
    try:
        r = subprocess.run(
            cmd, cwd=str(UPLOADER_DIR),
            capture_output=True, text=True, timeout=600,
        )
        return r.returncode, (r.stdout + "\n" + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "timeout"


def main() -> None:
    # ── Write cookie file ────────────────────────────────────────────────────
    cookie_content = base64.b64decode(COOKIE_B64).decode()
    cookie_file    = COOKIES_DIR / f"tiktok_session-{ACCOUNT}"
    cookie_file.write_text(cookie_content)
    print(f"[+] Cookie written: {cookie_file}")

    send_telegram(
        f"🚀 *GitHub Actions*: импорт запущен\n"
        f"👤 Аккаунт: `{ACCOUNT}`\n"
        f"🔗 Источник: `{SOURCE_URL}`"
    )

    # ── Get video list (with descriptions, oldest first) ────────────────────
    send_telegram(f"📋 Получаю список видео из `{SOURCE_URL}`…")

    proc = run_ydlp([
        "--skip-download",
        "--print", "%(id)s\t%(title)s\t%(description)s\t%(url)s",
        "--playlist-reverse",
        "--no-warnings",
        "--quiet",
        SOURCE_URL,
    ], timeout=180)

    if proc.returncode != 0 and not proc.stdout.strip():
        send_telegram(
            f"❌ Не удалось получить список видео:\n"
            f"```\n{proc.stderr[-500:]}\n```"
        )
        sys.exit(1)

    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    if not lines:
        send_telegram("❌ Плейлист пуст или ссылка не поддерживается.")
        sys.exit(1)

    total = len(lines)
    send_telegram(f"✅ Найдено видео: *{total}*\nНачинаю загрузку от самых старых…")

    # ── Process each video ───────────────────────────────────────────────────
    for idx, line in enumerate(lines, 1):
        parts = line.split("\t", 3)
        vid_title = parts[1].strip() if len(parts) > 1 else f"video_{idx}"
        vid_desc  = parts[2].strip() if len(parts) > 2 else ""
        vid_url   = parts[3].strip() if len(parts) > 3 else SOURCE_URL

        # Use original description as caption; fall back to title.
        # TikTok caption limit: 2200 chars.
        caption = (vid_desc if vid_desc else vid_title)[:2200].strip() or f"video_{idx}"

        send_telegram(f"📥 *{idx}/{total}* Скачиваю:\n_{vid_title[:120]}_")

        out_file = VIDEOS_DIR / f"bulk_{ACCOUNT}_{idx}.mp4"

        dl = run_ydlp([
            "--no-playlist",
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(out_file),
            "--no-warnings",
            "--quiet",
            vid_url,
        ])

        if dl.returncode != 0 or not out_file.exists():
            send_telegram(
                f"⚠️ *{idx}/{total}* Не удалось скачать, пропускаю.\n"
                f"```\n{dl.stderr[-200:]}\n```"
            )
            continue

        send_telegram(f"📤 *{idx}/{total}* Публикую в TikTok…")

        rc, output = run_upload(str(out_file), caption)
        out_file.unlink(missing_ok=True)

        if rc == -1:
            send_telegram(f"⚠️ *{idx}/{total}* Таймаут публикации, пропускаю.")
        elif rc == 0:
            send_telegram(f"✅ *{idx}/{total}* Опубликовано: _{vid_title[:100]}_")
        else:
            send_telegram(
                f"⚠️ *{idx}/{total}* Ошибка публикации:\n"
                f"```\n{output[-300:]}\n```"
            )

    send_telegram(
        f"🎉 *Импорт завершён!*\n"
        f"Всего видео: *{total}* из `{SOURCE_URL}`."
    )


if __name__ == "__main__":
    main()
