from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psycopg

from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows

SSL_MODE_MAP = {
    "required": "require",
    "preferred": "prefer",
    "disabled": "disable",
    "verify-full": "verify-full",
}


def connection_options(target: Target) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "autocommit": True,
        "connect_timeout": target.connect_timeout_seconds,
    }
    if target.host:
        kwargs["host"] = target.host
    if target.port:
        kwargs["port"] = target.port
    if target.user:
        kwargs["user"] = target.user
    if target.database:
        kwargs["dbname"] = target.database
    if target.password is not None:
        kwargs["password"] = target.password
    if target.ssl_mode:
        kwargs["sslmode"] = SSL_MODE_MAP[target.ssl_mode]
    if target.service:
        kwargs["service"] = target.service
    if target.ssl_ca:
        kwargs["sslrootcert"] = str(Path(target.ssl_ca).expanduser())
    elif target.ssl_mode == "verify-full" and not os.getenv("PGSSLROOTCERT"):
        root = (
            Path(os.environ["APPDATA"]) / "postgresql" / "root.crt"
            if os.name == "nt" and os.getenv("APPDATA")
            else Path.home() / ".postgresql" / "root.crt"
        )
        kwargs["sslrootcert"] = str(root) if root.exists() else "system"
    options = ["-c default_transaction_read_only=on"]
    if target.query_timeout_seconds:
        options.append(f"-c statement_timeout={target.query_timeout_seconds * 1000}")
    kwargs["options"] = " ".join(options)
    return kwargs


def execute_postgres_query(target: Target, sql: str) -> QueryExecutionResult:
    with (
        psycopg.connect(**connection_options(target)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(sql)
        return fetch_limited_rows(cursor, target.max_rows or 200)
