import argparse
from pathlib import Path
import sys

from hasc.db import (
    create_user,
    get_connection,
    get_user_by_username,
    init_db,
)
from hasc.db.connection import get_default_db_path
from hasc.accounts import set_or_create_owner, validate_username
from hasc.web.errors import AppError
from hasc.web.security import (
    generate_temporary_password,
    hash_password,
    temp_password_expires_at,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="create_user",
        description="Create users and admin accounts for HASC VN.",
    )
    parser.add_argument(
        "--username",
        required=True,
        help="Username for the new account.",
    )
    parser.add_argument(
        "--role",
        default=None,
        choices=["operator", "employee", "boss", "admin"],
        help="Role for the new account.",
    )
    parser.add_argument(
        "--owner",
        action="store_true",
        default=False,
        help="Create an owner admin or transfer ownership to an existing admin.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Account password. If omitted, a temporary password is generated.",
    )
    parser.add_argument(
        "--temporary",
        dest="must_change_password",
        action="store_true",
        default=None,
        help="Mark password as temporary (user must change on first login).",
    )
    parser.add_argument(
        "--no-temporary",
        dest="must_change_password",
        action="store_false",
        help="Mark password as permanent.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.owner and not args.role:
        print("Error: --role is required when --owner is not specified.", file=sys.stderr)
        return 1

    if args.owner and args.role and args.role != "admin":
        print("Error: Owner account must have role 'admin'.", file=sys.stderr)
        return 1

    db_path = Path(args.db) if args.db else get_default_db_path()
    conn = init_db(db_path)
    try:
        if args.owner:
            try:
                user_id, temp_pw, created = set_or_create_owner(
                    conn,
                    username=args.username,
                    password=args.password,
                )
            except AppError as e:
                print(f"Error: {e.message}", file=sys.stderr)
                return 1
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

            clean_username = args.username.strip().lower()
            if created:
                print(f"Owner admin '{clean_username}' created successfully (ID: {user_id}).")
            else:
                print(f"Ownership transferred to admin '{clean_username}' (ID: {user_id}).")
            print(f"Temporary password: {temp_pw}")
            print("Note: User must change password upon first login.")
            return 0

        try:
            username = validate_username(args.username)
        except AppError as e:
            print(f"Error: {e.message}", file=sys.stderr)
            return 1

        existing = get_user_by_username(conn, username)
        if existing:
            print(f"Error: User '{username}' already exists.", file=sys.stderr)
            return 1

        password = args.password
        if password is None:
            password = generate_temporary_password(10)
            # Generated passwords default to temporary
            must_change = 1 if args.must_change_password is None else int(args.must_change_password)
        else:
            # If password was provided and --temporary/--no-temporary not specified, default to permanent (0)
            must_change = 0 if args.must_change_password is None else int(args.must_change_password)

        pw_hash = hash_password(password)
        temp_expiry = temp_password_expires_at() if must_change else None
        user_id = create_user(
            conn,
            username=username,
            password_hash=pw_hash,
            role=args.role,
            active=1,
            must_change_password=must_change,
            temp_password_expires_at=temp_expiry,
        )

        print(f"User '{username}' created successfully (ID: {user_id}, Role: {args.role}).")
        if must_change:
            print(f"Temporary password: {password}")
            print("Note: User must change password upon first login.")
        elif args.password is None:
            print(f"Password: {password}")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
