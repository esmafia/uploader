#!/usr/bin/env python3
"""
TikTok uploader — запускается в GitHub Actions.
Читает куки из TIKTOK_COOKIES (JSON-массив объектов куки).
Скачанное видео должно быть в ./video.mp4 (рабочий каталог GitHub Actions).
"""
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("uploader")

VIDEO_PATH = Path("video.mp4")
TIKTOK_UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"


def load_cookies() -> list:
    """Читает куки из переменной окружения TIKTOK_COOKIES (JSON-массив)."""
    raw = os.environ.get("TIKTOK_COOKIES", "")
    if not raw:
        logger.error(
            "❌ Переменная TIKTOK_COOKIES не задана!\n"
            "Добавьте её в GitHub → Settings → Secrets and variables → Actions."
        )
        sys.exit(1)

    try:
        cookies = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"❌ TIKTOK_COOKIES содержит невалидный JSON: {exc}")
        sys.exit(1)

    if not isinstance(cookies, list) or len(cookies) == 0:
        logger.error("❌ TIKTOK_COOKIES должен быть непустым JSON-массивом объектов.")
        sys.exit(1)

    logger.info(f"✅ Загружено {len(cookies)} куки.")
    return cookies


def run_upload():
    if not VIDEO_PATH.exists():
        logger.error(f"❌ Файл видео не найден: {VIDEO_PATH.resolve()}")
        sys.exit(1)

    logger.info(f"📂 Файл видео: {VIDEO_PATH.resolve()} ({VIDEO_PATH.stat().st_size // 1024} KB)")

    cookies = load_cookies()

    from playwright.sync_api import sync_playwright

    try:
        from playwright_stealth import stealth_sync
        HAS_STEALTH = True
    except ImportError:
        HAS_STEALTH = False
        logger.warning("playwright-stealth не установлен — работаем без него.")

    with sync_playwright() as pw:
        logger.info("🌐 Запуск Chromium (headless)...")
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-extensions",
                "--no-first-run",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        try:
            context.add_cookies(cookies)
            logger.info("🍪 Куки добавлены в контекст браузера.")
        except Exception as exc:
            logger.error(f"❌ Ошибка при добавлении куки: {exc}")
            browser.close()
            sys.exit(1)

        page = context.new_page()

        if HAS_STEALTH:
            try:
                stealth_sync(page)
                logger.info("🛡 Stealth-режим активен.")
            except Exception as exc:
                logger.warning(f"Stealth не применился: {exc}")

        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {
                get: () => [{filename:'Chrome PDF Plugin'},{filename:'Chrome PDF Viewer'}]
            });
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = {runtime: {}, loadTimes: () => {}, csi: () => {}, app: {}};
        """)

        try:
            logger.info("🌐 Открываю TikTok Studio Upload...")
            page.goto(TIKTOK_UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(5)

            if any(x in page.url for x in ["login", "passport", "/account/"]):
                logger.error(
                    "❌ Не авторизован — TikTok перенаправил на страницу входа.\n"
                    "Обновите TIKTOK_COOKIES в GitHub Secrets."
                )
                browser.close()
                sys.exit(1)

            logger.info(f"📄 Текущий URL: {page.url}")

            logger.info("🔍 Ищу поле загрузки файла...")
            file_input = None

            try:
                page.wait_for_selector('input[type="file"]', timeout=15_000, state="attached")
                file_input = page.locator('input[type="file"]').first
                logger.info("✅ Поле загрузки найдено на основной странице.")
            except Exception:
                pass

            if file_input is None:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        frame.wait_for_selector('input[type="file"]', timeout=4_000, state="attached")
                        file_input = frame.locator('input[type="file"]').first
                        logger.info(f"✅ Поле загрузки найдено в iframe: {frame.url}")
                        break
                    except Exception:
                        continue

            if file_input is None:
                logger.error(
                    "❌ Поле загрузки файла не найдено.\n"
                    "Возможные причины:\n"
                    "  • Куки устарели — обновите TIKTOK_COOKIES\n"
                    "  • TikTok изменил интерфейс\n"
                    "  • Бот не прошёл проверку"
                )
                page.screenshot(path="debug_no_input.png", full_page=True)
                browser.close()
                sys.exit(1)

            logger.info(f"📤 Загружаю файл: {VIDEO_PATH}")
            file_input.set_input_files(str(VIDEO_PATH.resolve()))
            logger.info("✅ Файл передан браузеру. Жду обработки TikTok...")
            time.sleep(8)

            logger.info("⏳ Ожидаю появления редактора описания (до 3 мин)...")
            caption_selectors = [
                '[data-e2e="caption-content"]',
                'div[class*="caption"] div[contenteditable="true"]',
                'div[class*="DraftEditor"] div[contenteditable="true"]',
                ".public-DraftEditor-content",
                'div[contenteditable="true"]',
            ]

            caption_el = None
            deadline = time.time() + 180
            while time.time() < deadline:
                for sel in caption_selectors:
                    try:
                        el = page.locator(sel).first
                        if el.is_visible(timeout=1_000):
                            caption_el = el
                            logger.info(f"✅ Редактор описания найден: {sel}")
                            break
                    except Exception:
                        pass
                if caption_el:
                    break

                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    for sel in caption_selectors:
                        try:
                            el = frame.locator(sel).first
                            if el.is_visible(timeout=800):
                                caption_el = el
                                logger.info(f"✅ Редактор описания в iframe: {sel}")
                                break
                        except Exception:
                            pass
                    if caption_el:
                        break

                if caption_el:
                    break
                logger.info("  ещё жду...")
                time.sleep(5)

            if caption_el is None:
                logger.warning(
                    "⚠️ Редактор описания не найден за 3 минуты — пропускаю описание."
                )
                page.screenshot(path="debug_no_caption.png", full_page=True)

            time.sleep(5)

            logger.info("🚀 Ищу кнопку «Опубликовать»...")
            post_selectors = [
                '[data-e2e="post-btn"]',
                'button[class*="post-btn"]',
                'button[class*="PostButton"]',
                'button[class*="submitButton"]',
                'button:has-text("Post")',
                'button:has-text("Publish")',
                'button:has-text("Опубликовать")',
            ]

            post_btn = None
            for sel in post_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=3_000):
                        post_btn = btn
                        logger.info(f"✅ Кнопка публикации найдена: {sel}")
                        break
                except Exception:
                    continue

            if post_btn is None:
                logger.error(
                    "❌ Кнопка «Опубликовать» не найдена.\n"
                    "Интерфейс TikTok Studio мог измениться."
                )
                page.screenshot(path="debug_no_postbtn.png", full_page=True)
                browser.close()
                sys.exit(1)

            logger.info("🖱 Нажимаю кнопку публикации...")
            post_btn.click()
            time.sleep(3)

            logger.info("⏳ Жду подтверждения публикации (до 60 сек)...")
            success = False
            for attempt in range(20):
                time.sleep(3)
                current_url = page.url
                if any(x in current_url for x in ["content", "creator", "profile", "success"]):
                    success = True
                    logger.info(f"✅ Перенаправлен на: {current_url}")
                    break
                try:
                    toast = page.locator(
                        '[class*="success"],[class*="Success"],[class*="toast"]'
                    ).first
                    if toast.is_visible(timeout=500):
                        success = True
                        logger.info("✅ Toast уведомление об успехе обнаружено.")
                        break
                except Exception:
                    pass
                logger.info(f"  попытка {attempt + 1}/20, URL: {current_url}")

        except Exception as exc:
            logger.exception(f"❌ Неожиданная ошибка: {exc}")
            try:
                page.screenshot(path="debug_error.png", full_page=True)
            except Exception:
                pass
            browser.close()
            sys.exit(1)

        browser.close()

        if success:
            logger.info("🎉 Видео успешно опубликовано в TikTok!")
            sys.exit(0)
        else:
            logger.warning(
                "⚠️ Статус публикации неизвестен.\n"
                "Проверьте TikTok Studio вручную: https://www.tiktok.com/tiktokstudio"
            )
            sys.exit(0)


if __name__ == "__main__":
    run_upload()
