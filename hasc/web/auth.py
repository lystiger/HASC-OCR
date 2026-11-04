from datetime import datetime, timezone
import sqlite3
from typing import Generator

from fastapi import Depends, Request, Response

from hasc.db import (
    delete_session,
    get_connection,
    get_session,
    get_user_by_id,
)
from hasc.web.errors import AppError
from hasc.web.security import (
    get_session_duration,
    is_session_expired,
)

COOKIE_NAME = "hasc_session"


def get_db(request: Request) -> Generator[sqlite3.Connection, None, None]:
    """Dependency yielding a connection to the application database."""
    db_path = getattr(request.app.state, "db_path", None)
    conn = get_connection(db_path, auto_migrate=False)
    try:
        yield conn
    finally:
        conn.close()


def get_session_id_from_request(request: Request) -> str | None:
    """Extract session token from cookie or Authorization header."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token:
        return cookie_token

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    return None


def get_current_user_optional(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict | None:
    """Retrieve current user if a valid active session exists."""
    session_id = get_session_id_from_request(request)
    if not session_id:
        return None

    session_row = get_session(conn, session_id)
    if not session_row:
        return None

    # Check expiration
    if is_session_expired(session_row["expires_at"]):
        delete_session(conn, session_id)
        return None

    user_row = get_user_by_id(conn, session_row["user_id"])
    if not user_row or user_row["active"] != 1:
        return None

    return dict(user_row)


def get_authenticated_user(
    user: dict | None = Depends(get_current_user_optional),
) -> dict:
    """Ensure user is authenticated with a valid session.
    Allows users who need to change temporary password.
    """
    if not user:
        raise AppError(
            code="unauthorized",
            message="Chưa đăng nhập hoặc phiên làm việc đã hết hạn",
            status_code=401,
        )
    return user


def get_active_user(
    user: dict = Depends(get_authenticated_user),
) -> dict:
    """Ensure user is authenticated AND has fulfilled mandatory temporary password change (Criterion 1)."""
    if user.get("must_change_password"):
        raise AppError(
            code="password_change_required",
            message="Cần đổi mật khẩu tạm thời trước khi tiếp tục",
            status_code=403,
        )
    return user


def set_session_cookie(
    response: Response,
    session_id: str,
    role: str,
    secure: bool = False,
) -> None:
    """Set the session cookie with HttpOnly, SameSite, and appropriate Max-Age."""
    duration = get_session_duration(role)
    max_age = int(duration.total_seconds())
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
