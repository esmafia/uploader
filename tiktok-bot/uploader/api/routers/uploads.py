"""Immediate (synchronous) upload endpoint.

Two content types:
  * multipart/form-data with a `video` file + form fields — dropped in here
    as a temp file, uploaded via the adapter, then the temp file is removed.
  * application/json with {youtube_url, ...} — resolves via yt-dlp then uploads.

Scheduling is NOT handled here — see /api/schedules.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import Account
from api.schemas import UploadOptions, UploadResponse, UploadYouTubeRequest
from api.services import tiktok_adapter, youtube

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _require_account(session: Session, username: str) -> Account:
    acct = session.exec(select(Account).where(Account.username == username)).first()
    if not acct:
        raise HTTPException(status_code=404, detail=f"account '{username}' not found")
    if not acct.has_valid_session:
        raise HTTPException(status_code=409, detail=f"account '{username}' has no valid session; re-login required")
    return acct


def _touch_last_used(session: Session, acct: Account) -> None:
    acct.last_used_at = now_utc()
    acct.updated_at = now_utc()
    session.add(acct)
    session.commit()


@router.post("/file", response_model=UploadResponse)
async def upload_file(
    username: str = Form(...),
    title: str = Form(..., max_length=2200),
    options_json: str = Form("{}"),
    video: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    acct = _require_account(session, username)
    try:
        options = UploadOptions.model_validate_json(options_json)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"invalid options_json: {e}")

    suffix = os.path.splitext(video.filename or "upload.mp4")[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await video.read())
        tmp.flush()
        tmp.close()
        ok = tiktok_adapter.upload_from_options(
            username, tmp.name, title, options.model_dump()
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    _touch_last_used(session, acct)
    return UploadResponse(ok=ok, message="upload completed" if ok else "upload failed")


@router.post("/youtube", response_model=UploadResponse)
def upload_youtube(
    payload: UploadYouTubeRequest,
    session: Session = Depends(get_session),
):
    acct = _require_account(session, payload.username)
    video_path = youtube.download(payload.youtube_url)
    ok = tiktok_adapter.upload_from_options(
        payload.username, video_path, payload.title, payload.options.model_dump()
    )
    _touch_last_used(session, acct)
    return UploadResponse(ok=ok, message="upload completed" if ok else "upload failed")
