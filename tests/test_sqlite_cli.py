from __future__ import annotations

import io
import json
import sqlite3
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
from uuid import uuid4

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.cli import main


def _test_temp_dir(name: str) -> Path:
    root = Path.cwd() / "test-output"
    root.mkdir(exist_ok=True)
    path = root / f"{name}-{uuid4().hex}"
    path.mkdir()
    return path


class SqliteCliTests(unittest.TestCase):
    def test_sqlite_query_returns_json_payload(self) -> None:
        temp_dir = _test_temp_dir("sqlite-query")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = temp_dir / "demo.db"
        self._seed_database(db_path)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "--engine",
                    "sqlite",
                    "--path",
                    str(db_path),
                    "SELECT id, name FROM users ORDER BY id",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["result"]["returned_row_count"], 2)
        self.assertEqual(payload["result"]["rows"][0], [1, "Alice"])

    def test_write_query_is_blocked_with_usage_exit_code(self) -> None:
        temp_dir = _test_temp_dir("sqlite-write-block")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        db_path = temp_dir / "demo.db"
        self._seed_database(db_path)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(
                [
                    "--engine",
                    "sqlite",
                    "--path",
                    str(db_path),
                    "INSERT INTO users (name) VALUES ('Mallory')",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("Only read-only", stderr.getvalue())

    @staticmethod
    def _seed_database(path: Path) -> None:
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO users (name) VALUES ('Alice')")
            connection.execute("INSERT INTO users (name) VALUES ('Bob')")
            connection.commit()
        finally:
            connection.close()
