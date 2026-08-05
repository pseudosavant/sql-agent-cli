from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.cli import main
from sql_agent.models import AppConfig, Defaults, Target

RUN_INTEGRATION = os.getenv("SQL_AGENT_CLI_INTEGRATION") == "1"


@unittest.skipUnless(
    RUN_INTEGRATION, "set SQL_AGENT_CLI_INTEGRATION=1 to run network integration tests"
)
class NetworkIntegrationTests(unittest.TestCase):
    def test_postgres_query_uses_real_driver_and_truncates_rows(self) -> None:
        payload = self._run_query(
            engine="postgres",
            host_env="SQL_AGENT_CLI_POSTGRES_HOST",
            port_env="SQL_AGENT_CLI_POSTGRES_PORT",
            database_env="SQL_AGENT_CLI_POSTGRES_DATABASE",
            user_env="SQL_AGENT_CLI_POSTGRES_USER",
            password_env="SQL_AGENT_CLI_POSTGRES_PASSWORD",
        )

        self.assertEqual(payload["target"]["engine"], "postgres")
        self.assertEqual(payload["result"]["returned_row_count"], 1)
        self.assertTrue(payload["result"]["truncated"])
        self.assertEqual(payload["result"]["rows"], [[1]])

    def test_mysql_query_uses_real_driver_and_truncates_rows(self) -> None:
        payload = self._run_query(
            engine="mysql",
            host_env="SQL_AGENT_CLI_MYSQL_HOST",
            port_env="SQL_AGENT_CLI_MYSQL_PORT",
            database_env="SQL_AGENT_CLI_MYSQL_DATABASE",
            user_env="SQL_AGENT_CLI_MYSQL_USER",
            password_env="SQL_AGENT_CLI_MYSQL_PASSWORD",
        )

        self.assertEqual(payload["target"]["engine"], "mysql")
        self.assertEqual(payload["result"]["returned_row_count"], 1)
        self.assertTrue(payload["result"]["truncated"])
        self.assertEqual(payload["result"]["rows"], [[1]])

    def test_config_check_connects_to_postgres_and_mysql(self) -> None:
        postgres_password = self._required_env("SQL_AGENT_CLI_POSTGRES_PASSWORD")
        mysql_password = self._required_env("SQL_AGENT_CLI_MYSQL_PASSWORD")
        app_config = AppConfig(
            defaults=Defaults(target="postgres-ci"),
            targets={
                "mysql-ci": Target(
                    name="mysql-ci",
                    engine="mysql",
                    host=self._required_env("SQL_AGENT_CLI_MYSQL_HOST"),
                    port=int(self._required_env("SQL_AGENT_CLI_MYSQL_PORT")),
                    database=self._required_env("SQL_AGENT_CLI_MYSQL_DATABASE"),
                    user=self._required_env("SQL_AGENT_CLI_MYSQL_USER"),
                    password=mysql_password,
                    ssl_mode="disabled",
                ),
                "postgres-ci": Target(
                    name="postgres-ci",
                    engine="postgres",
                    host=self._required_env("SQL_AGENT_CLI_POSTGRES_HOST"),
                    port=int(self._required_env("SQL_AGENT_CLI_POSTGRES_PORT")),
                    database=self._required_env("SQL_AGENT_CLI_POSTGRES_DATABASE"),
                    user=self._required_env("SQL_AGENT_CLI_POSTGRES_USER"),
                    password=postgres_password,
                    ssl_mode="disabled",
                ),
            },
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("sql_agent.cli.load_config", return_value=app_config),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["config", "check", "--all", "--format", "json"])

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(postgres_password, stdout.getvalue())
        self.assertNotIn(mysql_password, stdout.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["checked"], 2)
        self.assertEqual(payload["succeeded"], 2)
        self.assertEqual([item["status"] for item in payload["results"]], ["ok", "ok"])

    def _run_query(
        self,
        *,
        engine: str,
        host_env: str,
        port_env: str,
        database_env: str,
        user_env: str,
        password_env: str,
    ) -> dict[str, object]:
        password = self._required_env(password_env)
        stdin = io.StringIO(password + "\n")
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = [
            "--engine",
            engine,
            "--host",
            self._required_env(host_env),
            "--port",
            self._required_env(port_env),
            "--database",
            self._required_env(database_env),
            "--user",
            self._required_env(user_env),
            "--ssl-mode",
            "disabled",
            "--password-stdin",
            "--max-rows",
            "1",
            "SELECT 1 AS value UNION ALL SELECT 2 AS value ORDER BY value",
        ]
        with (
            patch("sys.stdin", stdin),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(argv)

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(password, stdout.getvalue())
        return json.loads(stdout.getvalue())

    def _required_env(self, name: str) -> str:
        value = os.getenv(name)
        self.assertTrue(
            value, f"Missing required integration-test environment variable: {name}"
        )
        return value or ""


if __name__ == "__main__":
    unittest.main()
