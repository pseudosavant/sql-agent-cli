from __future__ import annotations

import io
import json
import shutil
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.cli import main
from sql_agent.validation import QueryValidationError, validate_query


def _test_temp_dir(name: str) -> Path:
    root = Path.cwd() / "test-output"
    root.mkdir(exist_ok=True)
    path = root / f"{name}-{uuid4().hex}"
    path.mkdir()
    return path


class ValidationHardeningTests(unittest.TestCase):
    def test_mutating_cte_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query(
                "WITH changed AS (DELETE FROM users RETURNING *) SELECT * FROM changed",
                "postgres",
            )

    def test_locking_select_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("SELECT * FROM users FOR UPDATE", "postgres")

    def test_multiple_statements_separated_by_comments_are_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("SELECT 1; /* boundary */ SELECT 2", "sqlite")

    def test_dangerous_postgres_file_function_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("SELECT pg_read_file('/etc/passwd')", "postgres")

    def test_advisory_lock_functions_are_rejected(self) -> None:
        for sql, engine in [
            ("SELECT GET_LOCK('agent-lock', 30)", "mysql"),
            ("SELECT pg_advisory_lock(42)", "postgres"),
        ]:
            with self.subTest(sql=sql), self.assertRaises(QueryValidationError):
                validate_query(sql, engine)

    def test_allowlisted_sqlite_pragma_is_allowed(self) -> None:
        validated = validate_query("PRAGMA table_info(users)", "sqlite")
        self.assertEqual(validated.statement_type, "pragma")

    def test_sqlite_pragma_assignment_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("PRAGMA user_version = 5", "sqlite")

    def test_sqlite_parenthesized_pragma_assignment_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("PRAGMA user_version(5)", "sqlite")

    def test_unallowlisted_sqlite_pragma_is_rejected(self) -> None:
        with self.assertRaises(QueryValidationError):
            validate_query("PRAGMA writable_schema", "sqlite")


class RuntimeHardeningTests(unittest.TestCase):
    def test_driver_failure_is_concise_redacted_and_has_runtime_exit_code(self) -> None:
        password = "super-secret-value"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(password + "\n")),
            patch(
                "sql_agent.engines.sqlite.execute_sqlite_query",
                side_effect=RuntimeError(f"connection failed password={password}"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "--engine",
                    "sqlite",
                    "--path",
                    "unused.db",
                    "--password-stdin",
                    "SELECT 1",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(password, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertIn("Query failed for target 'ephemeral' (sqlite)", stderr.getvalue())

    def test_sqlite_query_timeout_is_enforced(self) -> None:
        temp_dir = _test_temp_dir("sqlite-timeout")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = temp_dir / "demo.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.commit()
        finally:
            connection.close()
        calls = 0

        def clock() -> float:
            nonlocal calls
            calls += 1
            return 0.0 if calls == 1 else 2.0

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("sql_agent.engines.sqlite.monotonic", side_effect=clock),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "--engine",
                    "sqlite",
                    "--path",
                    str(db_path),
                    "--query-timeout-seconds",
                    "1",
                    (
                        "WITH RECURSIVE counter(value) AS "
                        "(SELECT 1 UNION ALL SELECT value + 1 FROM counter WHERE value < 1000000) "
                        "SELECT SUM(value) FROM counter"
                    ),
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("SQLite query exceeded the 1-second timeout", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_successful_json_contract_remains_stable(self) -> None:
        temp_dir = _test_temp_dir("sqlite-json-contract")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = temp_dir / "demo.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO users (name) VALUES ('Alice')")
            connection.commit()
        finally:
            connection.close()

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "--engine",
                    "sqlite",
                    "--path",
                    str(db_path),
                    "SELECT id, name FROM users",
                ]
            )

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(set(payload), {"target", "query", "result"})
        self.assertEqual(
            set(payload["result"]),
            {"columns", "rows", "returned_row_count", "truncated"},
        )


if __name__ == "__main__":
    unittest.main()
