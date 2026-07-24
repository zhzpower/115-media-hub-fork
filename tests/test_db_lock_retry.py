import sqlite3
import unittest
from unittest.mock import patch

from app import db


class DbLockRetryTest(unittest.TestCase):
    def test_retry_sqlite_locked_retries_then_returns(self):
        calls = []

        def flaky_operation():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        with patch.object(db.time, "sleep") as sleep_mock:
            result = db.retry_sqlite_locked(flaky_operation, attempts=2)

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 2)
        sleep_mock.assert_called_once()

    def test_retry_sqlite_locked_does_not_retry_other_operational_errors(self):
        calls = []

        def failing_operation():
            calls.append(1)
            raise sqlite3.OperationalError("no such table: missing")

        with self.assertRaises(sqlite3.OperationalError), patch.object(db.time, "sleep") as sleep_mock:
            db.retry_sqlite_locked(failing_operation, attempts=3)

        self.assertEqual(len(calls), 1)
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
