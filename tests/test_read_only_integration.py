from __future__ import annotations

import os
import unittest

from psycopg import DatabaseError as PostgresDatabaseError
from pymysql.err import DatabaseError as MySqlDatabaseError

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.engines.mysql import execute_mysql_query
from sql_agent.engines.postgres import execute_postgres_query
from sql_agent.models import Target

RUN_INTEGRATION = os.getenv("SQL_AGENT_CLI_INTEGRATION") == "1"


@unittest.skipUnless(
    RUN_INTEGRATION, "set SQL_AGENT_CLI_INTEGRATION=1 to run network integration tests"
)
class ReadOnlyIntegrationTests(unittest.TestCase):
    def test_postgres_session_rejects_writes_when_validation_is_bypassed(self) -> None:
        target = self._target("postgres")
        status = execute_postgres_query(target, "SHOW transaction_read_only")
        self.assertEqual(status.rows, [("on",)])
        with self.assertRaises(PostgresDatabaseError):
            execute_postgres_query(
                target, "CREATE TABLE sql_agent_cli_should_not_exist (id integer)"
            )

    def test_mysql_session_rejects_writes_when_validation_is_bypassed(self) -> None:
        target = self._target("mysql")
        status = execute_mysql_query(target, "SELECT @@SESSION.transaction_read_only")
        self.assertEqual(status.rows, [(1,)])
        with self.assertRaises(MySqlDatabaseError):
            execute_mysql_query(
                target, "CREATE TABLE sql_agent_cli_should_not_exist (id integer)"
            )

    def _target(self, engine: str) -> Target:
        prefix = f"SQL_AGENT_CLI_{engine.upper()}"
        return Target(
            name=f"{engine}-ci",
            engine=engine,
            host=self._required_env(f"{prefix}_HOST"),
            port=int(self._required_env(f"{prefix}_PORT")),
            database=self._required_env(f"{prefix}_DATABASE"),
            user=self._required_env(f"{prefix}_USER"),
            password=self._required_env(f"{prefix}_PASSWORD"),
            ssl_mode="disabled",
            connect_timeout_seconds=8,
            query_timeout_seconds=15,
            max_rows=200,
        )

    def _required_env(self, name: str) -> str:
        value = os.getenv(name)
        self.assertTrue(
            value, f"Missing required integration-test environment variable: {name}"
        )
        return value or ""


if __name__ == "__main__":
    unittest.main()
