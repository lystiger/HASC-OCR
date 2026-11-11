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


# A password the user chooses: at least this long, and not the one it replaces (a temporary one included).
MIN_PASSWORD_LENGTH = 6


def new_password_problem(new_password: str, current_hash: str | None = None) -> str | None:
    """Why this new password is refused, in Vietnamese; None when it is accepted."""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu mới phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if current_hash and verify_password(new_password, current_hash):
        return "Mật khẩu mới phải khác mật khẩu hiện tại."
    return None


# Session durations as defined in spec (Section 4 & criterion 2)
# Operators: 30 days
# Office (Employee, Boss) & Admin: 8 hours
OPERATOR_SESSION_DURATION = timedelta(days=30)
OFFICE_SESSION_DURATION = timedelta(hours=8)
TEMP_PASSWORD_DURATION = timedelta(hours=72)
ONLINE_THRESHOLD = timedelta(minutes=5)


def now() -> datetime:
    """Current UTC time helper, used for sessions, temporary-password expiry and last seen."""
    return datetime.now(timezone.utc)


def temp_password_expires_at(from_time: datetime | None = None) -> str:
    """Return ISO string for temporary password expiry (now + 72 h)."""
    base = from_time if from_time is not None else now()
    return (base + TEMP_PASSWORD_DURATION).isoformat()


def get_session_duration(role: str) -> timedelta:
    """Return session lifetime according to user role."""
    if role == "operator":
        return OPERATOR_SESSION_DURATION
    return OFFICE_SESSION_DURATION


def is_session_expired(expires_at: str | datetime, current_time: datetime | None = None) -> bool:
    """Check whether a session timestamp is in the past."""
    if current_time is None:
        current_time = now()
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


def is_temp_password_expired(expires_at: str | datetime | None, current_time: datetime | None = None) -> bool:
    """Check whether a temporary password has expired."""
    if not expires_at:
        return False
    return is_session_expired(expires_at, current_time=current_time)


def is_online(last_seen_at: str | datetime | None, current_time: datetime | None = None) -> bool:
    """Check whether an account was seen within the last 5 minutes."""
    if not last_seen_at:
        return False
    if current_time is None:
        current_time = now()
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    if isinstance(last_seen_at, str):
        iso_str = last_seen_at.replace("Z", "+00:00")
        try:
            seen_dt = datetime.fromisoformat(iso_str)
        except ValueError:
            return False
    else:
        seen_dt = last_seen_at

    if seen_dt.tzinfo is None:
        seen_dt = seen_dt.replace(tzinfo=timezone.utc)

    return (current_time - seen_dt) <= ONLINE_THRESHOLD


def generate_session_id() -> str:
    """Generate a high-entropy URL-safe session token."""
    return secrets.token_urlsafe(32)


def generate_temporary_password(length: int = 10) -> str:
    """Generate a readable temporary password containing letters and digits."""
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))
