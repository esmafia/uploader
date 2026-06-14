"""Atomic claim + heartbeat + upload.

Pulled out of main.py so it can be unit-tested directly (freeze time, patch
the adapter, patch the heartbeat). The real service composes these.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import timedelta
from typing import Optional

from sqlalchemy import and_, update
from sqlmodel import Session, select

import api.db as _api_db
from api.db import now_utc
from api.models import Account, ScheduledUpload
from api.services import tiktok_adapter, youtube

log = logging.getLogger("scheduler.worker")

STALE_RUNNING_AFTER = timedelta(minutes=5)
MAX_ATTEMPTS = 3
HEARTBEAT_INTERVAL = 30.0  # seconds


def reclaim_stale(session: Session) -> int:
    """Rows stuck in 'running' beyond STALE_RUNNING_AFTER are put back into
    'pending' if attempts remain, else marked 'failed'. Called on startup
    and at the top of every poll cycle."""
    cutoff = now_utc() - STALE_RUNNING_AFTER
    rows = session.exec(
        select(ScheduledUpload).where(
            and_(
                ScheduledUpload.status == "running",
                ScheduledUpload.heartbeat_at < cutoff,
            )
        )
    ).all()
    count = 0
    for r in rows:
        if r.attempts >= MAX_ATTEMPTS:
            r.status = "failed"
            r.result_text = f"exceeded max attempts ({MAX_ATTEMPTS}) after stall"
        else:
            r.status = "pending"
        r.updated_at = now_utc()
        session.add(r)
        count += 1
    if count:
        session.commit()
    return count


def claim_next_due(session: Session) -> Optional[ScheduledUpload]:
    """Pick one pending row whose scheduled_for is past and atomically claim
    it. Returns None if nothing is due. The UPDATE...WHERE status='pending'
    clause is the race guard: only one worker wins if two race."""
    now = now_utc()
    row = session.exec(
        select(ScheduledUpload)
        .where(ScheduledUpload.status == "pending")
        .where(ScheduledUpload.scheduled_for <= now)
        .order_by(ScheduledUpload.scheduled_for)
        .limit(1)
    ).first()
    if not row:
        return None

    # Atomic claim — WHERE status='pending' ensures we can't double-claim.
    result = session.exec(
        update(ScheduledUpload)
        .where(ScheduledUpload.id == row.id)
        .where(ScheduledUpload.status == "pending")
        .values(
            status="running",
            attempts=ScheduledUpload.attempts + 1,
            heartbeat_at=now,
            updated_at=now,
        )
    )
    session.commit()
    if result.rowcount != 1:
        return None  # lost the race
    # Re-read after the update so the caller sees fresh values.
    return session.get(ScheduledUpload, row.id)


class Heartbeat:
    """Background thread that bumps heartbeat_at every HEARTBEAT_INTERVAL
    while an upload is in flight. Lets reclaim_stale distinguish a wedged
    worker from a healthy long-running upload."""

    def __init__(self, schedule_id: int):
        self.schedule_id = schedule_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=HEARTBEAT_INTERVAL + 5)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            try:
                with Session(_api_db.engine) as s:
                    row = s.get(ScheduledUpload, self.schedule_id)
                    if not row or row.status != "running":
                        return
                    row.heartbeat_at = now_utc()
                    s.add(row)
                    s.commit()
            except Exception:  # pragma: no cover - best-effort
                log.exception("heartbeat write failed")


def run_one(row: ScheduledUpload) -> tuple[bool, str]:
    """Execute a single claimed row. Returns (ok, message).
    Isolated + pure in/out so tests can drive it without touching the loop."""
    with Session(_api_db.engine) as s:
        acct = s.get(Account, row.account_id)
        if not acct:
            return False, "account row missing"
        username = acct.username

    # Resolve source → local path.
    if row.source_type == "youtube":
        try:
            video_path = youtube.download(row.source_ref)
        except Exception as e:
            return False, f"youtube download failed: {e}"
    else:
        video_path = row.source_ref

    options = json.loads(row.options_json or "{}")
    try:
        ok = tiktok_adapter.upload_from_options(username, video_path, row.title, options)
    except Exception as e:
        return False, f"upload raised: {e}"
    return ok, "succeeded" if ok else "upload returned false"


def finalize(schedule_id: int, ok: bool, message: str) -> None:
    with Session(_api_db.engine) as s:
        row = s.get(ScheduledUpload, schedule_id)
        if not row:
            return
        row.status = "succeeded" if ok else ("failed" if row.attempts >= MAX_ATTEMPTS else "pending")
        row.result_text = message
        row.updated_at = now_utc()
        # Update the account's last_used_at only on success.
        if ok:
            acct = s.get(Account, row.account_id)
            if acct:
                acct.last_used_at = now_utc()
                s.add(acct)
        s.add(row)
        s.commit()


def tick() -> bool:
    """One poll cycle. Returns True if a job was processed, False if idle.
    Visible in tests so they can drive the worker deterministically."""
    with Session(_api_db.engine) as s:
        reclaim_stale(s)
        claimed = claim_next_due(s)
    if not claimed:
        return False

    hb = Heartbeat(claimed.id)
    hb.start()
    try:
        ok, msg = run_one(claimed)
    finally:
        hb.stop()
    finalize(claimed.id, ok, msg)
    return True


def loop(poll_interval: float = 30.0) -> None:  # pragma: no cover - driven by tick() in tests
    log.info("scheduler loop starting, poll_interval=%ss", poll_interval)
    while True:
        try:
            busy = tick()
        except Exception:
            log.exception("tick failed")
            busy = False
        if not busy:
            time.sleep(poll_interval)
