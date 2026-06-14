"""Pydantic request/response schemas — separate from SQLModel tables so we can
validate input (e.g. reject past scheduled_for, reject non-YouTube URLs) without
polluting the ORM layer. These schemas are what appears in `/docs`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------- accounts ---------------------------------------------------------

class AccountRead(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    cookie_path: str
    has_valid_session: bool
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime]

    model_config = {"from_attributes": True}


class AccountCreate(BaseModel):
    # Register an existing cookie file (produced by CLI login or a previous
    # noVNC login) against a username. The cookie file must already exist in
    # CookiesDir; this endpoint does not *create* a login session.
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")
    display_name: Optional[str] = Field(default=None, max_length=128)


class AccountUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)


# ---------- uploads (immediate) ---------------------------------------------

class UploadOptions(BaseModel):
    """Mirrors CLI upload flags. All optional with sensible defaults."""
    allow_comment: Literal[0, 1] = 1
    allow_duet: Literal[0, 1] = 0
    allow_stitch: Literal[0, 1] = 0
    visibility_type: Literal[0, 1] = 0  # 0=public, 1=private
    brand_organic_type: Literal[0, 1] = 0
    branded_content_type: Literal[0, 1] = 0
    ai_label: Literal[0, 1] = 0
    proxy: str = ""


class UploadYouTubeRequest(BaseModel):
    username: str
    title: str = Field(min_length=1, max_length=2200)
    youtube_url: str
    options: UploadOptions = Field(default_factory=UploadOptions)

    @field_validator("youtube_url")
    @classmethod
    def _youtube(cls, v: str) -> str:
        if not is_youtube_url(v):
            raise ValueError("youtube_url must be a valid YouTube URL")
        return v


class UploadResponse(BaseModel):
    ok: bool
    message: str


# ---------- schedules --------------------------------------------------------

class ScheduledUploadCreate(BaseModel):
    username: str
    title: str = Field(min_length=1, max_length=2200)
    source_type: Literal["local", "youtube"]
    source_ref: str  # absolute path (local) or YouTube URL
    scheduled_for: datetime  # ISO-8601; must have tzinfo
    options: UploadOptions = Field(default_factory=UploadOptions)

    @model_validator(mode="after")
    def _validate(self) -> "ScheduledUploadCreate":
        # Normalize to UTC
        if self.scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for must include timezone info")
        if self.scheduled_for <= datetime.now(timezone.utc):
            raise ValueError("scheduled_for must be in the future")
        if self.source_type == "youtube" and not is_youtube_url(self.source_ref):
            raise ValueError("source_ref must be a valid YouTube URL when source_type='youtube'")
        if self.options.visibility_type == 1:
            # Mirrors tiktok.py:77 — TikTok rejects scheduled private posts
            raise ValueError("private videos (visibility_type=1) cannot be scheduled")
        return self


class ScheduledUploadUpdate(BaseModel):
    scheduled_for: Optional[datetime] = None
    title: Optional[str] = Field(default=None, max_length=2200)
    status: Optional[Literal["pending", "cancelled"]] = None  # only safe transitions

    @model_validator(mode="after")
    def _validate(self) -> "ScheduledUploadUpdate":
        if self.scheduled_for is not None:
            if self.scheduled_for.tzinfo is None:
                raise ValueError("scheduled_for must include timezone info")
            if self.scheduled_for <= datetime.now(timezone.utc):
                raise ValueError("scheduled_for must be in the future")
        return self


class ScheduledUploadRead(BaseModel):
    id: int
    account_id: int
    source_type: str
    source_ref: str
    title: str
    options_json: str
    scheduled_for: datetime
    status: str
    result_text: Optional[str]
    attempts: int
    heartbeat_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- videos / browse --------------------------------------------------

class VideoFileInfo(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


# ---------- login (noVNC) ----------------------------------------------------

class LoginBrowserStartRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.\-]+$")


class LoginBrowserStartResponse(BaseModel):
    session_id: str
    vnc_url: str


class LoginBrowserCompleteRequest(BaseModel):
    """Payload the noVNC control server POSTs back to the API when it detects
    the sessionid cookie. The API is responsible for writing the pickle and
    updating the account row — noVNC stays stateless."""
    cookies: list[dict]


class LoginSessionRead(BaseModel):
    id: str
    username: str
    status: str
    vnc_url: Optional[str]
    error: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ---------- shared helpers ---------------------------------------------------

# Mirrors tiktok_uploader/Video.py:75 domain list as a single regex.
_YT_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)[\w\-]+",
    re.IGNORECASE,
)


def is_youtube_url(value: str) -> bool:
    return bool(_YT_URL_RE.match(value.strip()))
