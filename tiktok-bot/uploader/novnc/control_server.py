"""noVNC control server.

The API does NOT drive Selenium directly — it POSTs here, we start Chromium
on the Xvfb display, poll for the TikTok sessionid cookie, then POST the
cookie payload back to the API's callback_url. The API is the only service
that writes the pickle, keeping this container stateless.

Single-slot queue: only one browser session at a time. A second start call
while one is active returns 409. This matches the realistic workload (a
single human logging in).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from tiktok_uploader.tiktok import wait_for_session_cookies

log = logging.getLogger("novnc.control")
app = FastAPI(title="noVNC control server")

_TIKTOK_LOGIN_URL = "https://www.tiktok.com/login"
_POLL_TIMEOUT_SECONDS = 600  # 10-min human login window


class _BrowserState:
    """Wraps the currently-active chromedriver session. At most one at a time.
    Guarded by `_lock` so concurrent requests see a consistent view."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.session_id: Optional[str] = None
        self.username: Optional[str] = None
        self.callback_url: Optional[str] = None
        self.status: str = "idle"  # idle|starting|active|completing|completed|failed
        self.error: Optional[str] = None
        self.driver = None
        self.thread: Optional[threading.Thread] = None

    def reset(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:  # pragma: no cover
                pass
        self.driver = None
        self.session_id = None
        self.username = None
        self.callback_url = None
        self.status = "idle"
        self.error = None
        self.thread = None


_state = _BrowserState()


class StartRequest(BaseModel):
    session_id: str
    username: str
    callback_url: str


def _launch_and_watch(sid: str, username: str, callback_url: str) -> None:
    """Worker thread: drive Chromium, wait for cookies, POST back to API."""
    import undetected_chromedriver as uc  # heavy import → lazy

    options = uc.ChromeOptions()
    # Container-safe flags (design review: /dev/shm should be tmpfs 512MB too).
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.binary_location = "/usr/bin/chromium"

    try:
        _state.driver = uc.Chrome(options=options, driver_executable_path="/usr/bin/chromedriver")
        _state.status = "active"
        _state.driver.get(_TIKTOK_LOGIN_URL)
    except Exception as e:
        log.exception("failed to start chromium")
        with _state.lock:
            _state.status = "failed"
            _state.error = str(e)
        return

    try:
        cookies = wait_for_session_cookies(
            _state.driver, poll_interval=2.0, timeout=_POLL_TIMEOUT_SECONDS
        )
    except TimeoutError as e:
        with _state.lock:
            _state.status = "failed"
            _state.error = str(e)
        return
    except Exception as e:
        log.exception("cookie polling errored")
        with _state.lock:
            _state.status = "failed"
            _state.error = str(e)
        return

    _state.status = "completing"
    try:
        with httpx.Client(timeout=30.0) as client:
            client.post(callback_url, json={"cookies": cookies}).raise_for_status()
        with _state.lock:
            _state.status = "completed"
    except httpx.HTTPError as e:
        log.exception("callback POST failed")
        with _state.lock:
            _state.status = "failed"
            _state.error = f"callback failed: {e}"


@app.post("/browser/start", status_code=status.HTTP_202_ACCEPTED)
def browser_start(req: StartRequest):
    with _state.lock:
        if _state.status in ("starting", "active", "completing"):
            raise HTTPException(
                status_code=409,
                detail=f"another login in progress (session {_state.session_id})",
            )
        _state.reset()
        _state.session_id = req.session_id
        _state.username = req.username
        _state.callback_url = req.callback_url
        _state.status = "starting"
        t = threading.Thread(
            target=_launch_and_watch,
            args=(req.session_id, req.username, req.callback_url),
            daemon=True,
        )
        _state.thread = t
        t.start()
    return {"session_id": req.session_id, "status": "starting"}


@app.get("/browser/status/{session_id}")
def browser_status(session_id: str):
    with _state.lock:
        if _state.session_id != session_id:
            raise HTTPException(status_code=404, detail="unknown session")
        return {
            "session_id": session_id,
            "status": _state.status,
            "error": _state.error,
        }


@app.delete("/browser/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def browser_stop(session_id: str):
    with _state.lock:
        if _state.session_id != session_id:
            return  # already gone, idempotent
        _state.reset()


@app.get("/health")
def health():
    return {"status": "ok", "browser_status": _state.status}
