from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from passlib.context import CryptContext

from .db import db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SESSION_COOKIE = "pota_session"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def get_user_by_username(username: str):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username=? AND active=1", (username,)
        ).fetchone()


def get_user_by_id(user_id: int):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id=? AND active=1", (user_id,)
        ).fetchone()


def current_user(request: Request) -> Optional[dict]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    row = get_user_by_id(uid)
    if not row:
        return None
    return dict(row)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non autenticato")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Solo admin")
    return user


def audit(user: dict | None, action: str, entity: str = None, entity_id=None, detail: str = None):
    from .db import get_conn
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO audit_log (user_id, username, action, entity, entity_id, detail)
               VALUES (?,?,?,?,?,?)""",
            (
                user["id"] if user else None,
                user["username"] if user else None,
                action,
                entity,
                str(entity_id) if entity_id is not None else None,
                detail,
            ),
        )
        conn.commit()
    finally:
        conn.close()
