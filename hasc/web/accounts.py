import re
import sqlite3
from typing import Any, Optional

from hasc.db import (
    create_event,
    create_user,
    delete_user_sessions,
    get_user_by_id,
    get_user_by_username,
    list_users,
    set_user_active,
    update_user_password,
    update_user_role,
)
from hasc.web.errors import AppError
from hasc.web import security
from hasc.web.security import (
    generate_temporary_password,
    hash_password,
    is_online,
    temp_password_expires_at,
)

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9._]+$")
ALLOWED_ROLES = {"employee", "boss", "admin"}

ROLE_LABELS: dict[str, str] = {
    "employee": "Văn phòng",
    "boss": "Ban giám đốc",
    "admin": "Quản trị",
    "operator": "Xưởng",
}


def get_account_actions(actor: dict, target: dict) -> dict:
    """Determine permitted actions and role choices for an actor on a target account."""
    actor_id = actor.get("id")
    actor_is_owner = bool(actor.get("is_owner"))
    target_id = target.get("id")
    target_is_owner = bool(target.get("is_owner"))
    target_role = target.get("role")
    target_active = target.get("active")

    # 1. Self or target is owner: no actions
    if actor_id == target_id or target_is_owner:
        return {
            "can_reset": False,
            "can_change_role": False,
            "can_deactivate": False,
            "can_reactivate": False,
            "can_logout": False,
            "available_roles": [],
        }

    # 2. Non-owner admin cannot manage any admin account
    if not actor_is_owner and target_role == "admin":
        return {
            "can_reset": False,
            "can_change_role": False,
            "can_deactivate": False,
            "can_reactivate": False,
            "can_logout": False,
            "available_roles": [],
        }

    # Available roles: only owner can grant admin; neither can grant operator
    if actor_is_owner:
        allowed = ["employee", "boss", "admin"]
    else:
        allowed = ["employee", "boss"]

    available_roles = [r for r in allowed if r != target_role]

    return {
        "can_reset": True,
        "can_change_role": len(available_roles) > 0,
        "can_deactivate": (target_active == 1),
        "can_reactivate": (target_active == 0),
        "can_logout": True,
        "available_roles": [(r, ROLE_LABELS.get(r, r)) for r in available_roles],
    }


def get_addable_roles(actor: dict) -> list[tuple[str, str]]:
    """Roles that the actor is allowed to assign when creating a new account."""
    actor_is_owner = bool(actor.get("is_owner"))
    if actor_is_owner:
        roles = ["employee", "boss", "admin"]
    else:
        roles = ["employee", "boss"]
    return [(r, ROLE_LABELS.get(r, r)) for r in roles]


def validate_username(username: str) -> str:
    """Validate username rules according to spec-1.1.0 section 4:
    - 3-32 characters from a-z, 0-9, . and _
    - Stored in lower case
    - Refusals in Vietnamese
    """
    if not isinstance(username, str):
        raise AppError(
            code="invalid_username",
            message="Tên đăng nhập không hợp lệ",
            status_code=400,
        )
    if " " in username:
        raise AppError(
            code="invalid_username",
            message="Tên đăng nhập không được chứa khoảng trắng",
            status_code=400,
        )
    if len(username) < 3 or len(username) > 32:
        raise AppError(
            code="invalid_username",
            message="Tên đăng nhập phải có từ 3 đến 32 ký tự",
            status_code=400,
        )
    if not USERNAME_REGEX.match(username):
        raise AppError(
            code="invalid_username",
            message="Tên đăng nhập chỉ được chứa các ký tự a-z, 0-9, dấu chấm và gạch dưới",
            status_code=400,
        )
    return username.lower()


def validate_owner_rules(
    actor: Any,
    target: Any | None,
    action: str,
    new_role: str | None = None,
    new_active: int | None = None,
) -> None:
    """Validate owner and self-action rules according to spec-1.1.0 section 4:
    - Exactly one account is the owner. Nobody else can deactivate, demote, reset or log it out.
    - Only the owner can give or take away the admin role. Other admins manage employee, boss and operator accounts only.
    - Nobody changes their own role, deactivates, resets or logs out themselves from the screen.
    """
    actor_dict = dict(actor) if actor is not None else {}
    target_dict = dict(target) if target is not None else None

    actor_id = actor_dict.get("id")
    actor_is_owner = bool(actor_dict.get("is_owner"))

    # For actions on an existing user:
    if target_dict is not None:
        target_id = target_dict.get("id")
        target_is_owner = bool(target_dict.get("is_owner"))
        target_role = target_dict.get("role")

        # 1. No self-actions from the screen / admin endpoints
        if actor_id == target_id:
            if action == "reset":
                raise AppError(
                    code="forbidden",
                    message="Không thể tự đặt lại mật khẩu cho chính mình",
                    status_code=403,
                )
            elif action == "role":
                raise AppError(
                    code="forbidden",
                    message="Không thể tự thay đổi vai trò của chính mình",
                    status_code=403,
                )
            elif action in ("deactivate", "reactivate"):
                raise AppError(
                    code="forbidden",
                    message="Không thể tự thay đổi trạng thái kích hoạt của chính mình",
                    status_code=403,
                )
            elif action == "logout":
                raise AppError(
                    code="forbidden",
                    message="Không thể tự đăng xuất qua giao diện quản trị",
                    status_code=403,
                )

        # 2. Owner protection: nobody can deactivate, demote, reset or log it out
        if target_is_owner:
            if action == "reset":
                raise AppError(
                    code="forbidden",
                    message="Không thể đặt lại mật khẩu của chủ sở hữu",
                    status_code=403,
                )
            elif action == "role":
                raise AppError(
                    code="forbidden",
                    message="Không thể thay đổi vai trò của chủ sở hữu",
                    status_code=403,
                )
            elif action in ("deactivate", "reactivate"):
                raise AppError(
                    code="forbidden",
                    message="Không thể thay đổi trạng thái kích hoạt của chủ sở hữu",
                    status_code=403,
                )
            elif action == "logout":
                raise AppError(
                    code="forbidden",
                    message="Không thể đăng xuất chủ sở hữu",
                    status_code=403,
                )

        # 3. Non-owner admin restrictions:
        # Cannot manage any admin account (reset, log out, deactivate, reactivate or change role)
        if not actor_is_owner:
            if target_role == "admin":
                if action == "reset":
                    raise AppError(
                        code="forbidden",
                        message="Quản trị viên không thể đặt lại mật khẩu của tài khoản quản trị khác",
                        status_code=403,
                    )
                elif action == "role":
                    raise AppError(
                        code="forbidden",
                        message="Quản trị viên không thể thay đổi vai trò của tài khoản quản trị khác",
                        status_code=403,
                    )
                elif action in ("deactivate", "reactivate"):
                    raise AppError(
                        code="forbidden",
                        message="Quản trị viên không thể thay đổi trạng thái kích hoạt của tài khoản quản trị khác",
                        status_code=403,
                    )
                elif action == "logout":
                    raise AppError(
                        code="forbidden",
                        message="Quản trị viên không thể đăng xuất tài khoản quản trị khác",
                        status_code=403,
                    )

            # Only owner can give or take away admin role
            if action == "role" and (new_role == "admin" or target_role == "admin"):
                raise AppError(
                    code="forbidden",
                    message="Chỉ chủ sở hữu mới có quyền thay đổi vai trò quản trị",
                    status_code=403,
                )

    # For adding accounts:
    if action == "add":
        if new_role == "admin" and not actor_is_owner:
            raise AppError(
                code="forbidden",
                message="Chỉ chủ sở hữu mới có quyền cấp vai trò quản trị",
                status_code=403,
            )


def create_account(
    conn: sqlite3.Connection,
    actor: dict,
    username: str,
    role: str,
) -> tuple[int, str]:
    """Create a new user account, enforcing username and owner rules."""
    if role == "operator":
        raise AppError(
            code="invalid_role",
            message="Không thể tạo tài khoản với vai trò xưởng (operator)",
            status_code=400,
        )
    if role not in ALLOWED_ROLES:
        raise AppError(
            code="invalid_role",
            message="Vai trò người dùng không hợp lệ",
            status_code=400,
        )

    validate_owner_rules(actor, target=None, action="add", new_role=role)

    clean_username = validate_username(username)
    existing = get_user_by_username(conn, clean_username)
    if existing:
        raise AppError(
            code="duplicate_username",
            message="Tên đăng nhập đã tồn tại",
            status_code=400,
        )

    temp_password = generate_temporary_password()
    pw_hash = hash_password(temp_password)
    expiry = temp_password_expires_at()

    with conn:
        user_id = create_user(
            conn,
            username=clean_username,
            password_hash=pw_hash,
            role=role,
            active=1,
            must_change_password=1,
            temp_password_expires_at=expiry,
            is_owner=0,
        )
        create_event(
            conn,
            user_id=actor["id"],
            action="account_add",
            field=f"user:{user_id}",
            old_value=None,
            new_value=role,
            at=security.now().isoformat(),
        )

    return user_id, temp_password


def reset_account_password(
    conn: sqlite3.Connection,
    actor: dict,
    target_user_id: int,
) -> str:
    """Reset password for a user, ending sessions and generating a temporary password."""
    target = get_user_by_id(conn, target_user_id)
    if not target:
        raise AppError(
            code="not_found",
            message="Người dùng không tồn tại",
            status_code=404,
        )

    validate_owner_rules(actor, target, action="reset")

    temp_password = generate_temporary_password()
    pw_hash = hash_password(temp_password)
    expiry = temp_password_expires_at()

    with conn:
        update_user_password(
            conn,
            target_user_id,
            pw_hash,
            must_change_password=1,
            temp_password_expires_at=expiry,
        )
        delete_user_sessions(conn, target_user_id)
        create_event(
            conn,
            user_id=actor["id"],
            action="account_reset",
            field=f"user:{target_user_id}",
            old_value=None,
            new_value=None,
            at=security.now().isoformat(),
        )

    return temp_password


def change_account_role(
    conn: sqlite3.Connection,
    actor: dict,
    target_user_id: int,
    new_role: str,
) -> None:
    """Change the role of a user, ending sessions."""
    target = get_user_by_id(conn, target_user_id)
    if not target:
        raise AppError(
            code="not_found",
            message="Người dùng không tồn tại",
            status_code=404,
        )

    if new_role == "operator":
        raise AppError(
            code="invalid_role",
            message="Không thể chuyển sang vai trò xưởng (operator)",
            status_code=400,
        )
    if new_role not in ALLOWED_ROLES:
        raise AppError(
            code="invalid_role",
            message="Vai trò người dùng không hợp lệ",
            status_code=400,
        )

    validate_owner_rules(actor, target, action="role", new_role=new_role)

    old_role = target["role"]
    if old_role != new_role:
        with conn:
            update_user_role(conn, target_user_id, new_role)
            delete_user_sessions(conn, target_user_id)
            create_event(
                conn,
                user_id=actor["id"],
                action="account_role",
                field=f"user:{target_user_id}",
                old_value=old_role,
                new_value=new_role,
                at=security.now().isoformat(),
            )


def set_account_active(
    conn: sqlite3.Connection,
    actor: dict,
    target_user_id: int,
    active: int,
) -> None:
    """Activate or deactivate a user account."""
    target = get_user_by_id(conn, target_user_id)
    if not target:
        raise AppError(
            code="not_found",
            message="Người dùng không tồn tại",
            status_code=404,
        )

    if active not in (0, 1):
        raise AppError(
            code="invalid_active",
            message="Trạng thái kích hoạt không hợp lệ",
            status_code=400,
        )

    action = "deactivate" if active == 0 else "reactivate"
    validate_owner_rules(actor, target, action=action, new_active=active)

    old_active = target["active"]
    if old_active != active:
        with conn:
            set_user_active(conn, target_user_id, active)
            if active == 0:
                delete_user_sessions(conn, target_user_id)
                create_event(
                    conn,
                    user_id=actor["id"],
                    action="account_deactivate",
                    field=f"user:{target_user_id}",
                    old_value="1",
                    new_value="0",
                    at=security.now().isoformat(),
                )
            else:
                create_event(
                    conn,
                    user_id=actor["id"],
                    action="account_reactivate",
                    field=f"user:{target_user_id}",
                    old_value="0",
                    new_value="1",
                    at=security.now().isoformat(),
                )


def logout_account(
    conn: sqlite3.Connection,
    actor: dict,
    target_user_id: int,
) -> None:
    """Log out a user by deleting all their sessions."""
    target = get_user_by_id(conn, target_user_id)
    if not target:
        raise AppError(
            code="not_found",
            message="Người dùng không tồn tại",
            status_code=404,
        )

    validate_owner_rules(actor, target, action="logout")

    with conn:
        delete_user_sessions(conn, target_user_id)
        create_event(
            conn,
            user_id=actor["id"],
            action="account_logout",
            field=f"user:{target_user_id}",
            old_value=None,
            new_value=None,
            at=security.now().isoformat(),
        )


def patch_account(
    conn: sqlite3.Connection,
    actor: dict,
    target_user_id: int,
    active: int | None = None,
    role: str | None = None,
    reset_password: bool | None = None,
) -> tuple[dict, str | None]:
    """Apply multiple patch updates atomically with pre-validation."""
    target = get_user_by_id(conn, target_user_id)
    if not target:
        raise AppError(
            code="not_found",
            message="Người dùng không tồn tại",
            status_code=404,
        )

    # 1. Validation phase
    if reset_password:
        validate_owner_rules(actor, target, action="reset")

    if role is not None:
        if role == "operator":
            raise AppError(
                code="invalid_role",
                message="Không thể chuyển sang vai trò xưởng (operator)",
                status_code=400,
            )
        if role not in ALLOWED_ROLES:
            raise AppError(
                code="invalid_role",
                message="Vai trò người dùng không hợp lệ",
                status_code=400,
            )
        validate_owner_rules(actor, target, action="role", new_role=role)

    if active is not None:
        if active not in (0, 1):
            raise AppError(
                code="invalid_active",
                message="Trạng thái kích hoạt không hợp lệ",
                status_code=400,
            )
        action = "deactivate" if active == 0 else "reactivate"
        validate_owner_rules(actor, target, action=action, new_active=active)

    # 2. Execution phase
    temp_password = None
    if reset_password:
        temp_password = reset_account_password(conn, actor, target_user_id)

    if role is not None and role != target["role"]:
        change_account_role(conn, actor, target_user_id, role)

    if active is not None and active != target["active"]:
        set_account_active(conn, actor, target_user_id, active)

    updated_target = get_user_by_id(conn, target_user_id)
    current_time = security.now()
    last_seen = updated_target["last_seen_at"]
    user_dict = {
        "id": updated_target["id"],
        "username": updated_target["username"],
        "role": updated_target["role"],
        "active": updated_target["active"],
        "must_change_password": bool(updated_target["must_change_password"]),
        "is_owner": bool(updated_target["is_owner"]),
        "last_seen_at": last_seen,
        "online": is_online(last_seen, current_time=current_time),
        "created_at": updated_target["created_at"],
    }
    return user_dict, temp_password


def list_accounts_with_status(conn: sqlite3.Connection) -> list[dict]:
    """Return all accounts with is_owner, last_seen_at, and online status."""
    users = list_users(conn)
    current_time = security.now()
    results = []
    for u in users:
        u_dict = dict(u)
        last_seen = u_dict.get("last_seen_at")
        results.append({
            "id": u_dict["id"],
            "username": u_dict["username"],
            "role": u_dict["role"],
            "active": u_dict["active"],
            "must_change_password": bool(u_dict.get("must_change_password")),
            "is_owner": bool(u_dict.get("is_owner", 0)),
            "last_seen_at": last_seen,
            "online": is_online(last_seen, current_time=current_time),
            "created_at": u_dict.get("created_at"),
        })
    return results


def list_account_events(conn: sqlite3.Connection) -> list[dict]:
    """Return account history events newest first, joining actor and target usernames."""
    cursor = conn.execute("""
        SELECT
            e.id,
            e.at,
            e.user_id,
            e.action,
            e.sheet_id,
            e.row_id,
            e.field,
            e.old_value,
            e.new_value,
            e.detail,
            actor.username AS actor_username
        FROM events e
        LEFT JOIN users actor ON e.user_id = actor.id
        WHERE e.action LIKE 'account_%'
        ORDER BY e.at DESC, e.id DESC
    """)
    rows = cursor.fetchall()
    results = []
    users_cache: dict[int, str | None] = {}
    for r in rows:
        item = dict(r)
        target_username = None
        target_id = None
        field = item.get("field")
        if field and field.startswith("user:"):
            try:
                target_id = int(field.split(":", 1)[1])
                if target_id not in users_cache:
                    u = get_user_by_id(conn, target_id)
                    users_cache[target_id] = u["username"] if u else None
                target_username = users_cache[target_id]
            except ValueError:
                pass
        item["target_user_id"] = target_id
        item["target_username"] = target_username
        results.append(item)
    return results


def set_or_create_owner(
    conn: sqlite3.Connection,
    username: str,
    password: str | None = None,
) -> tuple[int, str, bool]:
    """Create a new owner admin or transfer ownership to an existing admin.

    According to spec-1.1.0 section 4 & 6:
    - Creates the account as an admin owner, or makes an existing admin the owner
    - In one transaction, clears owner from old owner and sets on target
    - Sets a new temporary password (with the 72 h expiry)
    - Ends the target's sessions
    - Old owner retains owner flag if failure occurs part-way

    Returns (user_id, temporary_password, created_new).
    """
    clean_username = validate_username(username)
    temp_pw = password if password is not None else generate_temporary_password(10)
    pw_hash = hash_password(temp_pw)
    expiry = temp_password_expires_at()

    with conn:
        cursor = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (clean_username,)
        )
        existing = cursor.fetchone()

        if existing:
            if existing["role"] != "admin":
                raise AppError(
                    code="invalid_role",
                    message=f"Tài khoản '{clean_username}' không phải là quản trị viên",
                    status_code=400,
                )
            target_id = existing["id"]
            # Clear owner flag first to satisfy users_one_owner partial unique index
            conn.execute("UPDATE users SET is_owner = 0 WHERE is_owner = 1")
            conn.execute(
                """
                UPDATE users
                SET is_owner = 1,
                    active = 1,
                    password_hash = ?,
                    must_change_password = 1,
                    temp_password_expires_at = ?
                WHERE id = ?
                """,
                (pw_hash, expiry, target_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
            return target_id, temp_pw, False
        else:
            conn.execute("UPDATE users SET is_owner = 0 WHERE is_owner = 1")
            created_at = security.now().isoformat()
            cursor = conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, role, active,
                    must_change_password, created_at, is_owner,
                    temp_password_expires_at, last_seen_at
                ) VALUES (?, ?, 'admin', 1, 1, ?, 1, ?, NULL)
                """,
                (clean_username, pw_hash, created_at, expiry),
            )
            new_id = cursor.lastrowid
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (new_id,))
            return new_id, temp_pw, True

