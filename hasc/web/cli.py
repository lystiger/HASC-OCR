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
        required=True,
        choices=["operator", "employee", "boss", "admin"],
        help="Role for the new account.",
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

    db_path = Path(args.db) if args.db else get_default_db_path()
    conn = init_db(db_path)
    try:
        existing = get_user_by_username(conn, args.username)
        if existing:
            print(f"Error: User '{args.username}' already exists.", file=sys.stderr)
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
            username=args.username,
            password_hash=pw_hash,
            role=args.role,
            active=1,
            must_change_password=must_change,
            temp_password_expires_at=temp_expiry,
        )

        print(f"User '{args.username}' created successfully (ID: {user_id}, Role: {args.role}).")
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
