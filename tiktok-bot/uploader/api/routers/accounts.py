"""CRUD on TikTok accounts. An account is a logical pairing of a username and
a pickled cookie file in CookiesDir. Creating an account here does NOT log in
— it registers a pickle that already exists (from CLI login or a prior noVNC
flow). Use /api/login/browser/* for the in-browser login flow.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from api.db import get_session, now_utc
from api.models import Account
from api.schemas import AccountCreate, AccountRead, AccountUpdate
from api.services import cookie_store

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountRead])
def list_accounts(session: Session = Depends(get_session)):
    return session.exec(select(Account).order_by(Account.username)).all()


@router.get("/{account_id}", response_model=AccountRead)
def get_account(account_id: int, session: Session = Depends(get_session)):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    return acct


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, session: Session = Depends(get_session)):
    # Require the cookie file to already exist. This endpoint is how CLI-
    # created cookies get promoted into the DB; it is NOT how new logins happen.
    if not cookie_store.exists(payload.username):
        raise HTTPException(
            status_code=400,
            detail=(
                f"No cookie file for '{payload.username}' at {cookie_store.cookie_file_path(payload.username)}. "
                "Run a login flow first (CLI or /api/login/browser/start)."
            ),
        )
    existing = session.exec(select(Account).where(Account.username == payload.username)).first()
    if existing:
        raise HTTPException(status_code=409, detail="account with that username already exists")

    acct = Account(
        username=payload.username,
        display_name=payload.display_name,
        cookie_path=cookie_store.cookie_file_path(payload.username),
        has_valid_session=cookie_store.has_valid_session(payload.username),
    )
    session.add(acct)
    session.commit()
    session.refresh(acct)
    return acct


@router.patch("/{account_id}", response_model=AccountRead)
def update_account(
    account_id: int,
    payload: AccountUpdate,
    session: Session = Depends(get_session),
):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    if payload.display_name is not None:
        acct.display_name = payload.display_name
    acct.updated_at = now_utc()
    session.add(acct)
    session.commit()
    session.refresh(acct)
    return acct


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acct = session.get(Account, account_id)
    if not acct:
        raise HTTPException(status_code=404, detail="account not found")
    # Delete DB row first — if cookie deletion fails afterwards the user can
    # still re-import. The reverse is harder to recover from.
    username = acct.username
    session.delete(acct)
    session.commit()
    cookie_store.delete(username)


@router.post("/import-from-disk", response_model=List[AccountRead])
def import_from_disk(session: Session = Depends(get_session)):
    """Scan CookiesDir for cookie files not yet in the DB and register them.
    Preserves the CLI-first workflow — users who log in with `python cli.py
    login -n foo` can click one button in the UI and see their account."""
    imported: list[Account] = []
    existing = {a.username for a in session.exec(select(Account)).all()}
    for username in cookie_store.list_usernames_on_disk():
        if username in existing:
            continue
        acct = Account(
            username=username,
            cookie_path=cookie_store.cookie_file_path(username),
            has_valid_session=cookie_store.has_valid_session(username),
        )
        session.add(acct)
        imported.append(acct)
    session.commit()
    for a in imported:
        session.refresh(a)
    return imported
