from datetime import datetime, timedelta, timezone
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Password hashing using argon2-cffi (Argon2id)
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using argon2-cffi."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against an argon2 hash."""
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Session durations as defined in spec (Section 4 & criterion 2)
# Operators: 30 days
# Office (Employee, Boss) & Admin: 8 hours
OPERATOR_SESSION_DURATION = timedelta(days=30)
OFFICE_SESSION_DURATION = timedelta(hours=8)


def get_session_duration(role: str) -> timedelta:
    """Return session lifetime according to user role."""
    if role == "operator":
        return OPERATOR_SESSION_DURATION
    return OFFICE_SESSION_DURATION


def is_session_expired(expires_at: str | datetime, current_time: datetime | None = None) -> bool:
    """Check whether a session timestamp is in the past."""
    if current_time is None:
        current_time = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    if isinstance(expires_at, str):
        # Handle 'Z' or ISO string
        iso_str = expires_at.replace("Z", "+00:00")
        exp_dt = datetime.fromisoformat(iso_str)
    else:
        exp_dt = expires_at

    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)

    return exp_dt < current_time


def generate_session_id() -> str:
    """Generate a high-entropy URL-safe session token."""
    return secrets.token_urlsafe(32)


def generate_temporary_password(length: int = 10) -> str:
    """Generate a readable temporary password containing letters and digits."""
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))
