from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .errors import UsageError, diagnostic
from .models import Target


def verify_target(target: Target) -> dict:
    report = {
        "status": "not_checked",
        "connection": "not_checked",
        "authentication": "not_checked",
        "read_only": "not_checked",
        "catalog": "not_checked",
        "transport": {"encrypted": None, "server_identity": "not_checked"},
        "table_access": "not_checked",
        "write_privileges": "not_checked",
    }
    try:
        if target.engine == "sqlite":
            from .engines.sqlite import open_connection

            if not target.path or not Path(target.path).expanduser().is_file():
                raise UsageError(
                    "The SQLite file is missing. Run uvx sql-agent-cli setup --engine sqlite --path PATH with an existing database."
                )
            with open_connection(target) as connection:
                report.update(
                    connection="passed",
                    authentication="not_required",
                    read_only="passed",
                )
                report["transport"] = {
                    "encrypted": None,
                    "server_identity": "not_applicable",
                }
                with closing(connection.cursor()) as cursor:
                    _catalog(
                        cursor,
                        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name LIMIT 21",
                        report,
                    )
                report["effective"] = {
                    "path": str(Path(target.path).expanduser().resolve())
                }
        elif target.engine == "postgres":
            import psycopg

            from .engines.postgres import connection_options

            with (
                psycopg.connect(**connection_options(target)) as connection,
                connection.cursor() as cursor,
            ):
                report.update(connection="passed", authentication="passed")
                encrypted = bool(connection.pgconn.ssl_in_use)
                report["transport"] = {
                    "encrypted": encrypted,
                    "server_identity": "verified"
                    if encrypted and target.ssl_mode == "verify-full"
                    else "not_verified",
                }
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone()[0] != "on":
                    raise RuntimeError("Read-only session was not established.")
                report["read_only"] = "passed"
                report["effective"] = {
                    "database": connection.info.dbname,
                    "user": connection.info.user,
                    "host": connection.info.host,
                    "port": connection.info.port,
                }
                _catalog(
                    cursor,
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name LIMIT 21",
                    report,
                )
        elif target.engine in {"mysql", "mariadb"}:
            from .engines.mysql import _tls_is_active, open_connection

            with (
                closing(open_connection(target)) as connection,
                connection.cursor() as cursor,
            ):
                report.update(connection="passed", authentication="passed")
                encrypted = _tls_is_active(connection)
                report["transport"] = {
                    "encrypted": encrypted,
                    "server_identity": "verified"
                    if encrypted and target.ssl_mode == "verify-full"
                    else "not_verified",
                }
                variable = (
                    "tx_read_only"
                    if target.engine == "mariadb"
                    else "transaction_read_only"
                )
                cursor.execute(
                    f"SELECT @@SESSION.{variable}, DATABASE(), CURRENT_USER()"
                )
                row = cursor.fetchone()
                if row[0] != 1:
                    raise RuntimeError("Read-only session was not established.")
                report["read_only"] = "passed"
                report["effective"] = {"database": row[1], "user": row[2]}
                _catalog(
                    cursor,
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_schema, table_name LIMIT 21",
                    report,
                )
        else:
            raise UsageError(
                "No engine resolved. Run uvx sql-agent-cli setup in your terminal."
            )
        report["status"] = "passed"
    except Exception as exc:  # noqa: BLE001 - Driver text must never escape the diagnostic boundary.
        report["status"] = "failed"
        report["error"] = diagnostic(exc, target.name)
    return report


def _catalog(cursor, sql: str, report: dict) -> None:
    cursor.execute("SELECT 1 AS sql_agent_cli_check")
    if cursor.fetchone()[0] != 1:
        raise RuntimeError("Unexpected connection check result.")
    cursor.execute(sql)
    rows = cursor.fetchmany(21)
    report.update(
        catalog="passed",
        visible_relations=min(len(rows), 20),
        more_relations=len(rows) > 20,
    )
    if not rows:
        report["notice"] = (
            "No relations were visible. The database may be empty or metadata visibility may be restricted."
        )
