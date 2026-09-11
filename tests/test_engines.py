from __future__ import annotations

import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.engines.mysql import (
    VerifiedConnection,
    execute_mysql_query,
    open_connection,
    verified_context,
)
from sql_agent.engines.postgres import connection_options, execute_postgres_query
from sql_agent.models import Target
from sql_agent.verification import verify_target


class EngineTests(unittest.TestCase):
    def test_verified_mysql_reuses_native_client_certificate_without_prompting(self):
        with tempfile.TemporaryDirectory() as directory:
            option = Path(directory) / "client.cnf"
            option.write_text(
                "[client]\nssl-ca=native-ca.pem\nssl-cert=client.pem\nssl-key=client-key.pem\nssl-password=dummy-key-passphrase\n"
            )
            with patch(
                "sql_agent.engines.mysql.ssl.create_default_context"
            ) as create_context:
                context = verified_context(Target(ssl_ca="explicit-ca.pem"), option)
            self.assertEqual(
                create_context.call_args.kwargs["cafile"], "explicit-ca.pem"
            )
            kwargs = context.load_cert_chain.call_args.kwargs
            self.assertEqual(kwargs["certfile"], "client.pem")
            self.assertEqual(kwargs["keyfile"], "client-key.pem")
            self.assertEqual(kwargs["password"](), "dummy-key-passphrase")

    def test_mysql_verify_full_requires_trust_and_hostname_verification(self):
        connection = MagicMock()
        with (
            patch(
                "sql_agent.engines.mysql.VerifiedConnection", return_value=connection
            ) as connect,
            patch("sql_agent.engines.mysql._tls_is_active", return_value=True),
            patch("sql_agent.engines.mysql.find_mysql_option_file", return_value=None),
        ):
            open_connection(
                Target(engine="mysql", host="db.example.test", ssl_mode="verify-full")
            )
        context = connect.call_args.kwargs["ssl"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_mysql_verify_full_refuses_non_tls_server_before_authentication(self):
        connection = object.__new__(VerifiedConnection)
        connection.server_capabilities = 0
        with (
            patch(
                "pymysql.connections.Connection._request_authentication"
            ) as authenticate,
            self.assertRaisesRegex(RuntimeError, "TLS"),
        ):
            connection._request_authentication()
        authenticate.assert_not_called()

    def test_postgres_verify_full_passes_ca_and_service(self):
        options = connection_options(
            Target(
                engine="postgres",
                ssl_mode="verify-full",
                ssl_ca="trusted.pem",
                service="reporting",
            )
        )
        self.assertEqual(options["sslmode"], "verify-full")
        self.assertEqual(options["sslrootcert"], "trusted.pem")
        self.assertEqual(options["service"], "reporting")

    def test_postgres_verifier_uses_one_session_and_bounded_catalog(self):
        connection = MagicMock()
        connection.info.dbname = "app"
        connection.info.user = "reader"
        connection.info.host = "db.example.test"
        connection.info.port = 5432
        connection.pgconn.ssl_in_use = True
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [("on",), (1,)]
        cursor.fetchmany.return_value = [("public", "sample")] * 21
        with patch("sql_agent.engines.postgres.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value = connection
            report = verify_target(Target(engine="postgres", ssl_mode="verify-full"))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["transport"]["server_identity"], "verified")
        self.assertEqual(report["visible_relations"], 20)
        self.assertTrue(report["more_relations"])
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(len(statements), 3)
        self.assertIn("LIMIT 21", statements[-1])
        self.assertIn("information_schema.tables", statements[-1])

    def test_verifier_rejects_writable_mysql_session_and_closes(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (0, "app", "reader")
        with (
            patch("sql_agent.engines.mysql.open_connection", return_value=connection),
            patch("sql_agent.engines.mysql._tls_is_active", return_value=True),
        ):
            report = verify_target(Target(engine="mysql", ssl_mode="verify-full"))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["catalog"], "not_checked")
        connection.close.assert_called_once_with()

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
