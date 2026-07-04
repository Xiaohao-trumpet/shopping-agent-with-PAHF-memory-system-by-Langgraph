"""Shared admin-auth FastAPI dependency.

Kept in its own module (rather than defined in ``main.py``) so
``realtime/api.py`` can gate its agent-console routes with the same admin
session check without a circular import -- ``main.py`` imports the realtime
router, so the router can't import back from ``main``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from .realtime.runtime import RT


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return token.strip()


def require_admin_token(authorization: Optional[str] = Header(default=None)) -> str:
    return _extract_bearer_token(authorization)


def require_admin(token: str = Depends(require_admin_token)) -> dict:
    if RT.admin_store is None:
        raise HTTPException(status_code=503, detail="Admin runtime is not ready")
    session = RT.admin_store.get_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")
    return session["user"]


def admin_session_for_token(token: Optional[str]) -> Optional[dict]:
    """Best-effort admin session lookup for contexts where raising isn't
    possible (WebSocket handshakes) -- returns None instead of raising."""
    if not token or RT.admin_store is None:
        return None
    session = RT.admin_store.get_session(token)
    return session["user"] if session else None
