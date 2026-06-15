#!/usr/bin/env python3
"""
Standalone single-video upload runner for GitHub Actions.

Required env vars:
  ACCOUNT        — TikTok account name (matches cookie filename)
  VIDEO_URL      — Direct URL to the video file (mp4)
  TITLE          — TikTok post caption
  VISIBILITY     — 0 = public, 1 = private (default 0)
  COOKIE_B64     — base64-encoded JSON array of cookie objects [{name,value,...}]
  CHAT_ID        — Telegram chat ID to send result to
  TELEGRAM_TOKEN — Telegram bot token
"""
from __future__ import annotations

import base64
import json
import os
import pickle
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
VIDEO_URL      = os.environ["VIDEO_URL"]
TITLE          = os.environ["TITLE"]
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


def download_video(url: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        print(f"[download error] {e}", file=sys.stderr)
        return False


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
    # Decode JSON cookie array and write as pickle file with .cookie extension
    # (required by tiktok_uploader/cookies.py → load_cookies_from_file)
    cookie_data = json.loads(base64.b64decode(COOKIE_B64).decode())
    cookie_file = COOKIES_DIR / f"tiktok_session-{ACCOUNT}.cookie"
    with open(cookie_file, "wb") as f:
        pickle.dump(cookie_data, f)
    print(f"[+] Cookie written: {cookie_file} ({len(cookie_data)} entries)")

    send_telegram(
        f"☁️ *GitHub Actions*: загружаю видео\n"
        f"👤 Аккаунт: `{ACCOUNT}`\n"
        f"✏️ Заголовок: {TITLE[:80]}"
    )

    out_file = VIDEOS_DIR / f"single_{ACCOUNT}.mp4"
    send_telegram("📥 Скачиваю видео…")

    if not download_video(VIDEO_URL, out_file):
        send_telegram("❌ Не удалось скачать видео. Попробуйте ещё раз.")
        sys.exit(1)

    send_telegram("📤 Публикую в TikTok…")
    rc, output = run_upload(str(out_file), TITLE)
    out_file.unlink(missing_ok=True)

    if rc == -1:
        send_telegram("❌ Таймаут публикации (>10 мин). Попробуйте позже.")
    elif rc == 0:
        send_telegram("🎉 *Видео успешно загружено в TikTok!*")
    else:
        preview = output[-400:] if len(output) > 400 else output
        send_telegram(
            f"❌ Ошибка публикации (код {rc}):\n"
            f"```\n{preview}\n```"
        )


if __name__ == "__main__":
    main()
