import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse

from hasc.db import get_user_by_id
from hasc.web.accounts import (
    ROLE_LABELS,
    change_account_role,
    create_account,
    account_was_used,
    delete_account,
    get_account_actions,
    get_addable_roles,
    list_account_events,
    list_accounts_with_status,
    logout_account,
    pending_reset_requests,
    reset_account_password,
    set_account_active,
)
from hasc.web.auth import get_db
from hasc.web.errors import AppError
from hasc.web.roles import require_role

router = APIRouter()

ACTION_LABELS = {
    "value_carry": "Cập nhật giá trị chép xuống",
    "account_add": "Thêm tài khoản",
    "account_reset": "Đặt lại mật khẩu",
    "account_role": "Thay đổi vai trò",
    "account_deactivate": "Vô hiệu hóa",
    "account_reactivate": "Kích hoạt lại",
    "account_logout": "Đăng xuất",
    "account_delete": "Xóa tài khoản",
    "account_rename": "Đổi tên đăng nhập",
    "account_reset_request": "Yêu cầu đặt lại mật khẩu",
    "account_password": "Tự đổi mật khẩu",
}


def format_event_detail(e: dict) -> str:
    action = e.get("action")
    old_val = e.get("old_value")
    new_val = e.get("new_value")

    if action == "account_add":
        role_label = ROLE_LABELS.get(new_val, new_val)
        return f"Vai trò ban đầu: {role_label}"
    elif action == "account_role":
        old_lbl = ROLE_LABELS.get(old_val, old_val)
        new_lbl = ROLE_LABELS.get(new_val, new_val)
        return f"{old_lbl} → {new_lbl}"
    elif action == "account_deactivate":
        return "Vô hiệu hóa tài khoản"
    elif action == "account_reactivate":
        return "Kích hoạt lại tài khoản"
    elif action == "account_reset":
        return "Đặt lại mật khẩu tạm thời"
    elif action == "account_logout":
        return "Đăng xuất các phiên làm việc"
    elif action == "account_delete":
        return "Xóa tài khoản chưa từng sử dụng"
    elif action == "account_rename":
        return f"{old_val} → {new_val}"
    elif action == "account_reset_request":
        return "Người dùng đề nghị quản trị viên đặt lại mật khẩu"
    elif action == "account_password":
        return "Người dùng tự đổi mật khẩu"
    return ""


def get_augmented_accounts(conn: sqlite3.Connection, actor: dict) -> list[dict]:
    raw_accounts = list_accounts_with_status(conn)
    pending = pending_reset_requests(conn)
    augmented = []
    for a in raw_accounts:
        acc = dict(a)
        acc["role_label"] = ROLE_LABELS.get(acc["role"], acc["role"])
        perms = get_account_actions(actor, acc)
        acc.update(perms)
        acc["can_delete"] = acc["can_delete"] and not account_was_used(conn, acc["id"])
        acc["reset_requested"] = pending.get(acc["id"])
        augmented.append(acc)
    return augmented


def get_augmented_events(conn: sqlite3.Connection) -> list[dict]:
    raw_events = list_account_events(conn)
    augmented = []
    for e in raw_events:
        item = dict(e)
        item["action_label"] = ACTION_LABELS.get(item["action"], item["action"])
        item["detail_text"] = format_event_detail(item)
        augmented.append(item)
    return augmented


# -----------------------------------------------------------------------------
# GET /admin/accounts & Partial
# -----------------------------------------------------------------------------


@router.get("/admin/accounts", response_class=HTMLResponse)
def admin_accounts_page(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = request.app.state.templates
    accounts = get_augmented_accounts(conn, admin)
    add_roles = get_addable_roles(admin)

    return templates.TemplateResponse(
        request=request,
        name="admin_accounts.html",
        context={
            "user": admin,
            "accounts": accounts,
            "add_roles": add_roles,
            "role_labels": ROLE_LABELS,
            "temp_password": None,
            "temp_user": None,
            "success_message": None,
            "error_message": None,
        },
    )


@router.get("/admin/accounts/partial", response_class=HTMLResponse)
def admin_accounts_partial(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = request.app.state.templates
    accounts = get_augmented_accounts(conn, admin)

    return templates.TemplateResponse(
        request=request,
        name="admin_accounts_table.html",
        context={
            "user": admin,
            "accounts": accounts,
            "role_labels": ROLE_LABELS,
        },
    )


# -----------------------------------------------------------------------------
# POST /admin/accounts (Unified action dispatch)
# -----------------------------------------------------------------------------


@router.post("/admin/accounts", response_class=HTMLResponse)
def admin_accounts_post(
    request: Request,
    action: str = Form(...),
    username: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    user_id: Optional[int] = Form(None),
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = request.app.state.templates
    temp_password = None
    temp_user = None
    success_message = None

    try:
        if action == "add":
            if not username or not role:
                raise AppError(
                    code="bad_request",
                    message="Thiếu tên đăng nhập hoặc vai trò",
                    status_code=400,
                )
            new_uid, temp_pw = create_account(
                conn=conn,
                actor=admin,
                username=username,
                role=role,
            )
            temp_password = temp_pw
            temp_user = username.strip().lower()
            success_message = f"Đã tạo tài khoản {temp_user} thành công."

        elif action == "reset":
            if not user_id:
                raise AppError(code="bad_request", message="Thiếu mã tài khoản", status_code=400)
            target = get_user_by_id(conn, user_id)
            if not target:
                raise AppError(code="not_found", message="Người dùng không tồn tại", status_code=404)
            temp_pw = reset_account_password(conn=conn, actor=admin, target_user_id=user_id)
            temp_password = temp_pw
            temp_user = target["username"]
            success_message = f"Đã đặt lại mật khẩu cho tài khoản {temp_user}."

        elif action == "role":
            if not user_id or not role:
                raise AppError(code="bad_request", message="Thiếu thông tin thay đổi vai trò", status_code=400)
            target = get_user_by_id(conn, user_id)
            if not target:
                raise AppError(code="not_found", message="Người dùng không tồn tại", status_code=404)
            change_account_role(conn=conn, actor=admin, target_user_id=user_id, new_role=role)
            target_name = target["username"]
            new_role_label = ROLE_LABELS.get(role, role)
            success_message = f"Đã đổi vai trò tài khoản {target_name} thành {new_role_label}."

        elif action == "deactivate":
            if not user_id:
                raise AppError(code="bad_request", message="Thiếu mã tài khoản", status_code=400)
            target = get_user_by_id(conn, user_id)
            if not target:
                raise AppError(code="not_found", message="Người dùng không tồn tại", status_code=404)
            set_account_active(conn=conn, actor=admin, target_user_id=user_id, active=0)
            target_name = target["username"]
            success_message = f"Đã vô hiệu hóa tài khoản {target_name}."

        elif action == "reactivate":
            if not user_id:
                raise AppError(code="bad_request", message="Thiếu mã tài khoản", status_code=400)
            target = get_user_by_id(conn, user_id)
            if not target:
                raise AppError(code="not_found", message="Người dùng không tồn tại", status_code=404)
            set_account_active(conn=conn, actor=admin, target_user_id=user_id, active=1)
            target_name = target["username"]
            success_message = f"Đã kích hoạt lại tài khoản {target_name}."

        elif action == "logout":
            if not user_id:
                raise AppError(code="bad_request", message="Thiếu mã tài khoản", status_code=400)
            target = get_user_by_id(conn, user_id)
            if not target:
                raise AppError(code="not_found", message="Người dùng không tồn tại", status_code=404)
            logout_account(conn=conn, actor=admin, target_user_id=user_id)
            target_name = target["username"]
            success_message = f"Đã đăng xuất tài khoản {target_name}."

        elif action == "delete":
            if not user_id:
                raise AppError(code="bad_request", message="Thiếu mã tài khoản", status_code=400)
            target_name = delete_account(conn=conn, actor=admin, target_user_id=user_id)
            success_message = f"Đã xóa tài khoản {target_name}."

        else:
            raise AppError(code="bad_request", message=f"Hành động '{action}' không hợp lệ", status_code=400)

        accounts = get_augmented_accounts(conn, admin)
        add_roles = get_addable_roles(admin)

        return templates.TemplateResponse(
            request=request,
            name="admin_accounts.html",
            context={
                "user": admin,
                "accounts": accounts,
                "add_roles": add_roles,
                "role_labels": ROLE_LABELS,
                "temp_password": temp_password,
                "temp_user": temp_user,
                "success_message": success_message,
                "error_message": None,
            },
        )

    except AppError as e:
        if e.status_code == 403:
            raise e
        accounts = get_augmented_accounts(conn, admin)
        add_roles = get_addable_roles(admin)
        return templates.TemplateResponse(
            request=request,
            name="admin_accounts.html",
            context={
                "user": admin,
                "accounts": accounts,
                "add_roles": add_roles,
                "role_labels": ROLE_LABELS,
                "temp_password": None,
                "temp_user": None,
                "success_message": None,
                "error_message": e.message,
            },
            status_code=e.status_code,
        )


# -----------------------------------------------------------------------------
# Explicit POST Sub-routes (for convenience / REST-style calls)
# -----------------------------------------------------------------------------


@router.post("/admin/accounts/add", response_class=HTMLResponse)
def admin_accounts_add_subroute(
    request: Request,
    username: str = Form(...),
    role: str = Form(...),
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="add",
        username=username,
        role=role,
        user_id=None,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/reset", response_class=HTMLResponse)
def admin_accounts_reset_subroute(
    request: Request,
    target_id: int,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="reset",
        username=None,
        role=None,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/role", response_class=HTMLResponse)
def admin_accounts_role_subroute(
    request: Request,
    target_id: int,
    role: str = Form(...),
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="role",
        username=None,
        role=role,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/deactivate", response_class=HTMLResponse)
def admin_accounts_deactivate_subroute(
    request: Request,
    target_id: int,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="deactivate",
        username=None,
        role=None,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/reactivate", response_class=HTMLResponse)
def admin_accounts_reactivate_subroute(
    request: Request,
    target_id: int,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="reactivate",
        username=None,
        role=None,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/logout", response_class=HTMLResponse)
def admin_accounts_logout_subroute(
    request: Request,
    target_id: int,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="logout",
        username=None,
        role=None,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


@router.post("/admin/accounts/{target_id}/delete", response_class=HTMLResponse)
def admin_accounts_delete_subroute(
    request: Request,
    target_id: int,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    return admin_accounts_post(
        request=request,
        action="delete",
        username=None,
        role=None,
        user_id=target_id,
        admin=admin,
        conn=conn,
    )


# -----------------------------------------------------------------------------
# GET /admin/accounts/history & Partial
# -----------------------------------------------------------------------------


@router.get("/admin/accounts/history", response_class=HTMLResponse)
def admin_accounts_history_page(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = request.app.state.templates
    events = get_augmented_events(conn)

    return templates.TemplateResponse(
        request=request,
        name="admin_accounts_history.html",
        context={
            "user": admin,
            "events": events,
            "role_labels": ROLE_LABELS,
        },
    )


@router.get("/admin/accounts/history/partial", response_class=HTMLResponse)
def admin_accounts_history_partial(
    request: Request,
    admin: dict = Depends(require_role("admin")),
    conn: sqlite3.Connection = Depends(get_db),
):
    templates = request.app.state.templates
    events = get_augmented_events(conn)

    return templates.TemplateResponse(
        request=request,
        name="admin_accounts_history_table.html",
        context={
            "user": admin,
            "events": events,
            "role_labels": ROLE_LABELS,
        },
    )
