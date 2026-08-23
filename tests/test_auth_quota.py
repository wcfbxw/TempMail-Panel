import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException


IMPORT_DB = Path(tempfile.gettempdir()) / f"lightningmail-import-{os.getpid()}.db"
os.environ["LM_DB_FILE"] = str(IMPORT_DB)

import app


class AuthQuotaTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        IMPORT_DB.unlink(missing_ok=True)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_FILE = str(Path(self.temp_dir.name) / "test.db")
        app.DAILY_FREE_LIMIT = 10
        app.init_db()
        with sqlite3.connect(app.DB_FILE) as conn:
            conn.execute("INSERT INTO domains (domain_name) VALUES ('example.test')")

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, username="tester", activation_code=""):
        return app.register(
            app.AuthModel(
                username=username,
                password="secret12",
                activation_code=activation_code,
            )
        )

    def create_mailbox(self, token, index):
        return app.create_single_account(
            app.AccountModel(
                email=f"mail{index}@example.test",
                password="MAILPASS",
            ),
            authorization=token,
        )

    def add_activation_code(self, code="LM-TEST-CODE-0001", max_uses=1):
        with sqlite3.connect(app.DB_FILE) as conn:
            conn.execute(
                """
                INSERT INTO activation_codes
                    (code_hash, code_hint, max_uses, used_count, created_at, is_active)
                VALUES (?, ?, ?, 0, ?, 1)
                """,
                (
                    app.activation_code_hash(code),
                    "****-0001",
                    max_uses,
                    app.utc_now_iso(),
                ),
            )
        return code

    def test_guest_cannot_create_mailbox(self):
        with self.assertRaises(HTTPException) as context:
            self.create_mailbox(None, 1)
        self.assertEqual(context.exception.status_code, 401)

    def test_basic_account_is_limited_to_ten_per_day(self):
        user = self.register()
        self.assertFalse(user["is_activated"])
        self.assertEqual(user["daily_remaining"], 10)

        for index in range(10):
            result = self.create_mailbox(user["token"], index)
        self.assertEqual(result["daily_remaining"], 0)

        with self.assertRaises(HTTPException) as context:
            self.create_mailbox(user["token"], 10)
        self.assertEqual(context.exception.status_code, 429)

        with sqlite3.connect(app.DB_FILE) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 10)
            stored_password = conn.execute(
                "SELECT password FROM web_users WHERE username='tester'"
            ).fetchone()[0]
        self.assertTrue(stored_password.startswith("pbkdf2_sha256$"))

    def test_valid_code_activates_during_registration(self):
        code = self.add_activation_code()
        user = self.register(activation_code=code)
        self.assertTrue(user["is_activated"])
        self.assertIsNone(user["daily_limit"])

        for index in range(12):
            self.create_mailbox(user["token"], index)

        with self.assertRaises(HTTPException) as context:
            self.register(username="second-user", activation_code=code)
        self.assertEqual(context.exception.status_code, 400)
        with sqlite3.connect(app.DB_FILE) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM web_users").fetchone()[0], 1)

    def test_invalid_code_rolls_back_registration(self):
        with self.assertRaises(HTTPException) as context:
            self.register(activation_code="LM-NOT-VALID")
        self.assertEqual(context.exception.status_code, 400)
        with sqlite3.connect(app.DB_FILE) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM web_users").fetchone()[0], 0)

    def test_user_can_activate_after_registration(self):
        code = self.add_activation_code()
        user = self.register()
        result = app.activate_account(
            app.ActivationRequest(activation_code=code),
            authorization=user["token"],
        )
        self.assertTrue(result["is_activated"])

    def test_legacy_plaintext_password_is_upgraded_on_login(self):
        with sqlite3.connect(app.DB_FILE) as conn:
            conn.execute(
                "INSERT INTO web_users (username, password, token) VALUES (?, ?, ?)",
                ("legacy", "oldpass1", "legacy-token"),
            )
        result = app.login(app.AuthModel(username="legacy", password="oldpass1"))
        self.assertEqual(result["token"], "legacy-token")
        with sqlite3.connect(app.DB_FILE) as conn:
            stored_password = conn.execute(
                "SELECT password FROM web_users WHERE username='legacy'"
            ).fetchone()[0]
        self.assertTrue(stored_password.startswith("pbkdf2_sha256$"))

    def test_sync_cannot_bypass_daily_limit(self):
        user = self.register()
        accounts = [
            app.AccountModel(
                email=f"sync{index}@example.test",
                password="MAILPASS",
            )
            for index in range(11)
        ]
        with self.assertRaises(HTTPException) as context:
            app.sync_accounts(app.SyncRequest(accounts=accounts), authorization=user["token"])
        self.assertEqual(context.exception.status_code, 429)
        with sqlite3.connect(app.DB_FILE) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
