from __future__ import annotations

from typing import Any

import pymysql

from ..config import find_mysql_option_file
from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows


def execute_mysql_query(target: Target, sql: str) -> QueryExecutionResult:
    kwargs: dict[str, Any] = {
        "autocommit": True,
        "connect_timeout": target.connect_timeout_seconds,
        "read_timeout": target.query_timeout_seconds,
        "write_timeout": target.query_timeout_seconds,
    }
    option_file = find_mysql_option_file()
    if option_file:
        kwargs["read_default_file"] = str(option_file)
    if target.host:
        kwargs["host"] = target.host
    if target.port:
        kwargs["port"] = target.port
    if target.user:
        kwargs["user"] = target.user
    if target.database:
        kwargs["database"] = target.database
    if target.password is not None:
        kwargs["password"] = target.password
    if target.ssl_mode in {"required", "preferred"}:
        # PyMySQL only enables TLS when "ssl" is truthy.
        kwargs["ssl"] = {"check_hostname": False}
    elif target.ssl_mode == "disabled":
        kwargs["ssl_disabled"] = True

    connection = pymysql.connect(**kwargs)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return fetch_limited_rows(cursor, target.max_rows or 200)
    finally:
        connection.close()
