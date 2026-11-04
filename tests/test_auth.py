from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from hasc.db import (
    create_session,
    create_user,
    get_connection,
    init_db,
)
from hasc.web.security import (
    get_session_duration,
    hash_password,
    is_session_expired,
    verify_password,
    OPERATOR_SESSION_DURATION,
    OFFICE_SESSION_DURATION,
)


class TestAuthSecurity(unittest.TestCase):
    def test_hash_and_verify_password(self):
        plain = "MySecretPass123"
        hashed = hash_password(plain)
        self.assertTrue(hashed.startswith("$argon2"))
        self.assertTrue(verify_password(plain, hashed))
        self.assertFalse(verify_password("WrongPass", hashed))

    def test_session_durations(self):
        self.assertEqual(get_session_duration("operator"), timedelta(days=30))
        self.assertEqual(get_session_duration("employee"), timedelta(hours=8))
        self.assertEqual(get_session_duration("boss"), timedelta(hours=8))
        self.assertEqual(get_session_duration("admin"), timedelta(hours=8))

    def test_criterion_2_operator_session_expiry(self):
        """Criterion 2: An operator session is still valid after 29 days and expired after 31."""
        now = datetime.now(timezone.utc)
        # Operator session lasts 30 days
        expiry = now + OPERATOR_SESSION_DURATION

        # After 29 days
        time_at_29_days = now + timedelta(days=29)
        self.assertFalse(is_session_expired(expiry.isoformat(), current_time=time_at_29_days))

        # After 31 days
        time_at_31_days = now + timedelta(days=31)
        self.assertTrue(is_session_expired(expiry.isoformat(), current_time=time_at_31_days))

    def test_criterion_2_office_session_expiry(self):
        """Criterion 2: An office session is valid after 7 hours and expired after 9."""
        now = datetime.now(timezone.utc)
        # Office session lasts 8 hours
        expiry = now + OFFICE_SESSION_DURATION

        # After 7 hours
        time_at_7_hours = now + timedelta(hours=7)
        self.assertFalse(is_session_expired(expiry.isoformat(), current_time=time_at_7_hours))

        # After 9 hours
        time_at_9_hours = now + timedelta(hours=9)
        self.assertTrue(is_session_expired(expiry.isoformat(), current_time=time_at_9_hours))
