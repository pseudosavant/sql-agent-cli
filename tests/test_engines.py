from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.engines.mysql import execute_mysql_query
from sql_agent.engines.postgres import execute_postgres_query
from sql_agent.models import Target


class EngineTests(unittest.TestCase):
    def test_postgres_enables_session_read_only_and_statement_timeout(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.description = [("value",)]
        cursor.fetchmany.return_value = [(1,)]

        with patch("sql_agent.engines.postgres.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value = connection
            result = execute_postgres_query(
                Target(
                    name="reporting",
                    engine="postgres",
                    host="db.example.com",
                    query_timeout_seconds=15,
                    max_rows=200,
                    ssl_mode="required",
                ),
                "SELECT 1",
            )

        self.assertEqual(result.rows, [(1,)])
        kwargs = connect.call_args.kwargs
        self.assertEqual(
            kwargs["options"],
            "-c default_transaction_read_only=on -c statement_timeout=15000",
        )
        self.assertEqual(kwargs["sslmode"], "require")

    def test_mysql_requires_read_only_session_and_confirmed_tls(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("Ssl_cipher", "TLS_AES_256_GCM_SHA384")
        cursor.description = [("value",)]
        cursor.fetchmany.return_value = [(1,)]

        with patch(
            "sql_agent.engines.mysql.pymysql.connect", return_value=connection
        ) as connect:
            result = execute_mysql_query(
                Target(
                    name="reporting",
                    engine="mysql",
                    query_timeout_seconds=15,
                    connect_timeout_seconds=8,
                    max_rows=200,
                    ssl_mode="required",
                ),
                "SELECT 1",
            )

        self.assertEqual(result.rows, [(1,)])
        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["init_command"], "SET SESSION TRANSACTION READ ONLY")
        self.assertEqual(kwargs["ssl"], {"check_hostname": True})
        cursor.execute.assert_any_call("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        cursor.execute.assert_any_call("SELECT 1")
        connection.close.assert_called_once_with()

    def test_mysql_required_rejects_plaintext_fallback(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("Ssl_cipher", "")

        with (
            patch("sql_agent.engines.mysql.pymysql.connect", return_value=connection),
            self.assertRaisesRegex(RuntimeError, "did not negotiate TLS"),
        ):
            execute_mysql_query(
                Target(
                    name="reporting",
                    engine="mysql",
                    query_timeout_seconds=15,
                    connect_timeout_seconds=8,
                    max_rows=200,
                    ssl_mode="required",
                ),
                "SELECT 1",
            )

        cursor.execute.assert_called_once_with("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        connection.close.assert_called_once_with()

    def test_mysql_preferred_allows_plaintext_fallback(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.description = [("value",)]
        cursor.fetchmany.return_value = [(1,)]

        with patch(
            "sql_agent.engines.mysql.pymysql.connect", return_value=connection
        ) as connect:
            execute_mysql_query(
                Target(
                    name="reporting",
                    engine="mysql",
                    query_timeout_seconds=15,
                    connect_timeout_seconds=8,
                    max_rows=200,
                    ssl_mode="preferred",
                ),
                "SELECT 1",
            )

        self.assertEqual(connect.call_args.kwargs["ssl"], {"check_hostname": True})
        cursor.execute.assert_called_once_with("SELECT 1")


if __name__ == "__main__":
    unittest.main()
