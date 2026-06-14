"""Browser-based login flow via noVNC.

Three endpoints make up the flow:

  POST /api/login/browser/start   → creates a LoginSession row, asks the
      noVNC control server to spin up Chromium at tiktok.com/login, returns
      a session_id + vnc_url the frontend embeds in an iframe.

  GET  /api/login/browser/{id}/events → SSE stream. The API polls the control
      server every ~1s and forwards status transitions to the client so the
      UI can flip from "Log in now" to "Session captured" without polling.

  POST /api/login/browser/{id}/complete → called by the noVNC control server
      once it detects the sessionid cookie. The API (not noVNC) writes the
      pickle to CookiesDir and upserts the Account row. Keeps noVNC stateless
      and puts all DB/filesystem writes in one service for easier testing.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from api.db import get_session, now_utc
from api.models import Account, LoginSession
from api.schemas import (
    LoginBrowserCompleteRequest,
    LoginBrowserStartRequest,
    LoginBrowserStartResponse,
    LoginSessionRead,
)
from api.services import cookie_store, novnc_client

router = APIRouter(prefix="/api/login/browser", tags=["login"])


def _callback_url() -> str:
    """URL the noVNC control server posts back to. Inside the compose network
    it's http://api:8000/api/login/browser/{id}/complete."""
    base = os.getenv("API_PUBLIC_URL", "http://api:8000")
    return base.rstrip("/")


@router.post("/start", response_model=LoginBrowserStartResponse, status_code=status.HTTP_201_CREATED)
def start_browser_login(
    payload: LoginBrowserStartRequest,
    session: Session = Depends(get_session),
):
    sid = uuid.uuid4().hex
    vnc_url = novnc_client.build_vnc_url(sid)
    row = LoginSession(
        id=sid,
        username=payload.username,
        status="pending",
        vnc_url=vnc_url,
    )
    session.add(row)
    session.commit()

    callback = f"{_callback_url()}/api/login/browser/{sid}/complete"
    try:
        novnc_client.start_browser(sid, payload.username, callback)
    except Exception as e:
        row.status = "failed"
        row.error = f"noVNC start failed: {e}"
        row.completed_at = now_utc()
        session.add(row)
        session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    row.status = "active"
    session.add(row)
    session.commit()
    return LoginBrowserStartResponse(session_id=sid, vnc_url=vnc_url)


@router.get("/{session_id}", response_model=LoginSessionRead)
def get_browser_session(session_id: str, session: Session = Depends(get_session)):
    row = session.get(LoginSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="login session not found")
    return row


@router.get("/{session_id}/events")
async def browser_events(session_id: str, request: Request):
    """Server-Sent Events: emits one event per status transition, terminates
    when the session enters a terminal state or the client disconnects.

    Note: nginx must have proxy_buffering off on this path (see webapp/nginx.conf)."""
    from api.db import engine  # local import so tests that override engine still see latest

    async def stream() -> AsyncIterator[dict]:
        last_status: str | None = None
        while True:
            if await request.is_disconnected():
                break
            # Each poll opens a fresh session — cheap under SQLite + WAL.
            # Use api.db.engine (not a captured reference) so test fixtures
            # that swap the engine are honored.
            import api.db as _api_db
            with Session(_api_db.engine) as s:
                row = s.get(LoginSession, session_id)
                if not row:
                    yield {"event": "error", "data": "unknown session"}
                    return
                if row.status != last_status:
                    last_status = row.status
                    yield {"event": "status", "data": row.status}
                if row.status in ("completed", "failed", "expired"):
                    return
            await asyncio.sleep(1.0)

    return EventSourceResponse(stream())


@router.post("/{session_id}/complete", response_model=LoginSessionRead)
def complete_browser_login(
    session_id: str,
    payload: LoginBrowserCompleteRequest,
    session: Session = Depends(get_session),
):
    """Called by the noVNC control server when it sees the sessionid cookie."""
    row = session.get(LoginSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="login session not found")
    if row.status == "completed":
        return row  # idempotent

    # Validate the payload actually contains a sessionid cookie.
    has_session = any(
        c.get("name") == "sessionid" and c.get("value") for c in payload.cookies
    )
    if not has_session:
        row.status = "failed"
        row.error = "no sessionid cookie in payload"
        row.completed_at = now_utc()
        session.add(row)
        session.commit()
        raise HTTPException(status_code=400, detail="no sessionid cookie in payload")

    # Write pickle → upsert account row.
    cookie_path = cookie_store.save(row.username, payload.cookies)
    acct = session.exec(select(Account).where(Account.username == row.username)).first()
    if acct is None:
        acct = Account(
            username=row.username,
            cookie_path=cookie_path,
            has_valid_session=True,
            last_used_at=now_utc(),
        )
        session.add(acct)
    else:
        acct.cookie_path = cookie_path
        acct.has_valid_session = True
        acct.last_used_at = now_utc()
        acct.updated_at = now_utc()
        session.add(acct)

    row.status = "completed"
    row.completed_at = now_utc()
    session.add(row)
    session.commit()
    session.refresh(row)
    # Best-effort teardown of the browser on the control container.
    novnc_client.stop_browser(session_id)
    return row


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_browser_login(session_id: str, session: Session = Depends(get_session)):
    row = session.get(LoginSession, session_id)
    if not row:
        raise HTTPException(status_code=404, detail="login session not found")
    if row.status in ("completed", "failed", "expired"):
        return Response(status_code=204)
    row.status = "expired"
    row.completed_at = now_utc()
    session.add(row)
    session.commit()
    novnc_client.stop_browser(session_id)
