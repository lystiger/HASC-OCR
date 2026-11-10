"""Each user's own Tài khoản page (P148): rename yourself, change your password, ask an admin for a reset.

The role is shown but can't be changed here; only an admin changes it on /admin/accounts.
A forgotten password is asked for from the login page, without logging in.
"""
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hasc.db import create_event, delete_user_sessions, get_user_by_id, get_user_by_username, update_user_password
from hasc.web import security
from hasc.web.accounts import (
    ROLE_LABELS,
    pending_reset_requests,
    rename_own_account,
    request_password_reset,
)
from hasc.web.auth import get_authenticated_user, get_db, get_session_id_from_request
from hasc.web.errors import AppError
from hasc.web.security import hash_password, verify_password

router = APIRouter()

OFFICE_ROLES = ("employee", "boss", "admin")
# Same answer whether or not the name exists, so the form can't be used to find accounts.
FORGOT_SENT = "Đã gửi yêu cầu. Quản trị viên sẽ đặt lại mật khẩu và đưa bạn mật khẩu tạm thời."
FORGOT_LIMIT = 5          # requests per address per window
FORGOT_WINDOW_S = 600
_forgot_seen: dict[str, list[float]] = {}
_forgot_lock = threading.Lock()


def _forgot_allowed(address: str) -> bool:
    """Per client address: the site is served by uvicorn directly, with no proxy in front."""
    now = time.monotonic()
    with _forgot_lock:
        if len(_forgot_seen) > 1000:  # drop addresses whose window has passed
            for key in [k for k, times in _forgot_seen.items() if now - times[-1] >= FORGOT_WINDOW_S]:
                del _forgot_seen[key]
        recent = [t for t in _forgot_seen.get(address, []) if now - t < FORGOT_WINDOW_S]
        if len(recent) >= FORGOT_LIMIT:
            _forgot_seen[address] = recent
            return False
        _forgot_seen[address] = recent + [now]
        return True


def self_user(user: dict = Depends(get_authenticated_user)) -> dict:
    """The logged-in user, once they have left their temporary password behind."""
    if user.get("must_change_password"):
        raise AppError(code="password_change_required", message="Vui lòng đổi mật khẩu tạm thời trước", status_code=403)
    return user


def _render(request: Request, conn: sqlite3.Connection, user: dict, status_code: int = 200, **messages):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="account.html",
        status_code=status_code,
        context={
            "user": user,
            "frame_template": "office_base.html" if user["role"] in OFFICE_ROLES else "base.html",
            "role_label": ROLE_LABELS.get(user["role"], user["role"]),
            "reset_pending": pending_reset_requests(conn).get(user["id"]),
            "success_message": messages.get("success"),
            "error_message": messages.get("error"),
        },
    )


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: dict = Depends(self_user),
                 conn: sqlite3.Connection = Depends(get_db)):
    return _render(request, conn, user, success=request.query_params.get("ok") and "Đã lưu thay đổi.")


@router.post("/account/username", response_class=HTMLResponse)
def account_rename(request: Request, username: str = Form(...), user: dict = Depends(self_user),
                   conn: sqlite3.Connection = Depends(get_db)):
    try:
        rename_own_account(conn, user, username)
    except AppError as error:
        return _render(request, conn, user, status_code=error.status_code, error=error.message)
    return RedirectResponse(url="/account?ok=1", status_code=303)


@router.post("/account/password", response_class=HTMLResponse)
def account_password(request: Request, old_password: str = Form(...), new_password: str = Form(...),
                     confirm_password: str = Form(...), user: dict = Depends(self_user),
                     conn: sqlite3.Connection = Depends(get_db)):
    if new_password != confirm_password:
        return _render(request, conn, user, status_code=400, error="Mật khẩu mới và xác nhận mật khẩu không khớp.")
    if not verify_password(old_password, user["password_hash"]):
        return _render(request, conn, user, status_code=400, error="Mật khẩu hiện tại không chính xác.")
    update_user_password(conn, user["id"], hash_password(new_password), must_change_password=0)
    delete_user_sessions(conn, user["id"], keep=get_session_id_from_request(request))
    with conn:
        create_event(conn, user_id=user["id"], action="account_password", field=f"user:{user['id']}",
                     at=security.now().isoformat())
    return RedirectResponse(url="/account?ok=1", status_code=303)


@router.post("/account/reset-request", response_class=HTMLResponse)
def account_reset_request(request: Request, user: dict = Depends(self_user),
                          conn: sqlite3.Connection = Depends(get_db)):
    request_password_reset(conn, user)
    return RedirectResponse(url="/account?ok=1", status_code=303)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request=request, name="forgot_password.html", context={"user": None, "message": None})


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(request: Request, username: str = Form(...), conn: sqlite3.Connection = Depends(get_db)):
    address = request.client.host if request.client else "?"
    if _forgot_allowed(address):
        found = get_user_by_username(conn, username.strip())
        # Unknown names run the same insert-if-none-waiting against id 0, which never exists,
        # so the answer takes the same work either way.
        target = dict(get_user_by_id(conn, found["id"])) if found and found["active"] else {"id": 0}
        if target["id"]:
            request_password_reset(conn, target)
        else:
            conn.execute("SELECT MAX(id) FROM events WHERE action = 'account_reset_request' AND field = 'user:0'").fetchone()
    return request.app.state.templates.TemplateResponse(
        request=request, name="forgot_password.html", context={"user": None, "message": FORGOT_SENT})
