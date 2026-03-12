from __future__ import annotations

from typing import Any

import psycopg

from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows

SSL_MODE_MAP = {
    "required": "require",
    "preferred": "prefer",
    "disabled": "disable",
}


def execute_postgres_query(target: Target, sql: str) -> QueryExecutionResult:
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
    if target.query_timeout_seconds:
        kwargs["options"] = f"-c statement_timeout={target.query_timeout_seconds * 1000}"

    with psycopg.connect(**kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return fetch_limited_rows(cursor, target.max_rows or 200)
