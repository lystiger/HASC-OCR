from typing import Callable, Set

from fastapi import Depends

from hasc.web.errors import AppError

# Section 4 Role Table permissions
ROLE_PERMISSIONS: dict[str, set[str]] = {
    # 1. Upload a photo, see the printed limits (spec 1.1.0 P128, P145: extended to office)
    "upload_photo": {"operator", "employee", "boss", "admin"},
    "view_limits": {"operator", "employee", "boss", "admin"},
    # 2. See the status of own uploads, and which rolls failed a check (spec 1.1.0 section 6)
    "view_own_uploads": {"operator", "employee", "boss", "admin"},
    # 3. See the status of all sheets, and photos
    "view_all_sheets": {"employee", "boss", "admin"},
    # 4. Pick up, download, correct, re-upload, confirm, retry, hand-enter
    "review_sheet": {"employee", "boss"},
    # 5. Mark an upload invalid
    "mark_invalid": {"employee", "boss"},
    # 6. Mark a machine or shift not running, with a reason
    "mark_not_running": {"operator", "employee", "boss"},
    # 7. See sheet numbers
    "view_sheet_numbers": {"employee", "boss", "admin"},
    # 8. See edit history
    "view_history": {"employee", "boss", "admin"},
    # 9. Add or deactivate accounts, reset passwords, log out any account
    "manage_users": {"admin"},
    # 10. See statistics (Admin full, Boss overdue only)
    "view_stats": {"boss", "admin"},
    # 11. The AI page: reads, accuracy, cost (P149); only admins set the budget
    "view_ai": {"boss", "admin"},
    "manage_ai_budget": {"admin"},
}

ROLE_GROUPS: dict[str, set[str]] = {
    "office": {"employee", "boss"},
    "all": {"operator", "employee", "boss", "admin"},
}


def resolve_allowed_roles(*roles_or_actions: str) -> set[str]:
    """Resolve role names, group names, or action names to a set of allowed role strings."""
    allowed: set[str] = set()
    for item in roles_or_actions:
        if item in ROLE_PERMISSIONS:
            allowed.update(ROLE_PERMISSIONS[item])
        elif item in ROLE_GROUPS:
            allowed.update(ROLE_GROUPS[item])
        elif item in {"operator", "employee", "boss", "admin"}:
            allowed.add(item)
        else:
            # Fallback treat as literal role
            allowed.add(item)
    return allowed


def require_role(*roles_or_actions: str) -> Callable:
    """FastAPI dependency factory enforcing roles and Section 4 role table.
    Returns 403 with spec error shape if the role is not permitted.
    """
    from hasc.web.auth import get_active_user

    allowed = resolve_allowed_roles(*roles_or_actions)

    def dependency(user: dict = Depends(get_active_user)) -> dict:
        user_role = user.get("role")
        if user_role not in allowed:
            raise AppError(
                code="forbidden",
                message="Không có quyền thực hiện thao tác này",
                status_code=403,
            )
        return user

    return dependency
