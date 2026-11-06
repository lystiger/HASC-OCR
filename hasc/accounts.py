"""Shared account logic module reused across web API, CLI, and account pages."""
from hasc.web.accounts import (
    ALLOWED_ROLES,
    change_account_role,
    create_account,
    list_account_events,
    list_accounts_with_status,
    logout_account,
    patch_account,
    reset_account_password,
    set_account_active,
    set_or_create_owner,
    validate_owner_rules,
    validate_username,
)

__all__ = [
    "ALLOWED_ROLES",
    "validate_username",
    "validate_owner_rules",
    "create_account",
    "reset_account_password",
    "change_account_role",
    "set_account_active",
    "logout_account",
    "patch_account",
    "list_accounts_with_status",
    "list_account_events",
    "set_or_create_owner",
]
