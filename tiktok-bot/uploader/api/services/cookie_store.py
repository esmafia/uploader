"""Filesystem-level cookie operations.

This is a thin wrapper around `tiktok_uploader.cookies` so the API layer has
one place to swap for tests and one place to enforce the `tiktok_session-{name}`
filename convention. The CLI continues to use the package directly.
"""
from __future__ import annotations

import os
from typing import Iterable

from tiktok_uploader.Config import Config
from tiktok_uploader.cookies import (
    delete_cookies_file,
    load_cookies_from_file,
    save_cookies_to_file,
)

COOKIE_PREFIX = "tiktok_session-"


def cookies_dir() -> str:
    return os.path.join(os.getcwd(), Config.get().cookies_dir)


def cookie_file_path(username: str) -> str:
    return os.path.join(cookies_dir(), f"{COOKIE_PREFIX}{username}.cookie")


def exists(username: str) -> bool:
    return os.path.isfile(cookie_file_path(username))


def load(username: str) -> list[dict]:
    return load_cookies_from_file(f"{COOKIE_PREFIX}{username}")


def save(username: str, cookies: Iterable[dict]) -> str:
    os.makedirs(cookies_dir(), exist_ok=True)
    save_cookies_to_file(list(cookies), f"{COOKIE_PREFIX}{username}")
    return cookie_file_path(username)


def delete(username: str) -> None:
    delete_cookies_file(f"{COOKIE_PREFIX}{username}")


def list_usernames_on_disk() -> list[str]:
    """All accounts currently represented by a pickle in CookiesDir —
    used by the "import from disk" endpoint to sync CLI-created cookies into
    the DB."""
    d = cookies_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fname in os.listdir(d):
        if fname.startswith(COOKIE_PREFIX) and fname.endswith(".cookie"):
            out.append(fname[len(COOKIE_PREFIX):-len(".cookie")])
    return sorted(out)


def has_valid_session(username: str) -> bool:
    """A cookie file has a valid session if it contains a `sessionid` entry."""
    try:
        cookies = load(username)
    except Exception:
        return False
    return any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
