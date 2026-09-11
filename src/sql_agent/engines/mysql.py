from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import pymysql
from pymysql.constants import CLIENT

from ..config import find_mysql_option_file
from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows


def verified_context(target: Target, option_file: Path | None):
    native = {}
    if option_file:
        from pymysql.optionfile import Parser

        parser = Parser()
        with option_file.open(encoding="utf-8") as handle:
            parser.read_file(handle)
        for key in ("ca", "capath", "cert", "key", "password"):
            if parser.has_option("client", "ssl-" + key):
                native[key] = parser.get("client", "ssl-" + key)
    ca = target.ssl_ca or native.get("ca")
    context = ssl.create_default_context(
        cafile=str(Path(ca).expanduser()) if ca else None,
        capath=str(Path(native["capath"]).expanduser())
        if native.get("capath")
        else None,
    )
    if native.get("cert"):
        context.load_cert_chain(
            certfile=str(Path(native["cert"]).expanduser()),
            keyfile=str(Path(native["key"]).expanduser())
            if native.get("key")
            else None,
            # Avoid OpenSSL prompting on stdin for an encrypted private key.
            password=lambda: native.get("password", ""),
        )
    return context


class VerifiedConnection(pymysql.connections.Connection):
    def _request_authentication(self):
        # PyMySQL otherwise allows a server without TLS to continue to auth.
        if not self.server_capabilities & CLIENT.SSL:
            raise RuntimeError("The server does not support required TLS.")
        return super()._request_authentication()


def open_connection(target: Target):
    kwargs: dict[str, Any] = {
        "autocommit": True,
        "connect_timeout": target.connect_timeout_seconds,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
        "read_timeout": target.query_timeout_seconds,
        "write_timeout": target.query_timeout_seconds,
    }
    option_file = find_mysql_option_file(target)
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
    if target.unix_socket:
        kwargs["unix_socket"] = target.unix_socket
    if target.ssl_mode == "verify-full":
        kwargs["ssl"] = verified_context(target, option_file)
    elif target.ssl_mode in {"required", "preferred"}:
        # PyMySQL only enables TLS when "ssl" is truthy.
        kwargs["ssl"] = {"check_hostname": True}
        if target.ssl_ca:
            kwargs["ssl"]["ca"] = str(Path(target.ssl_ca).expanduser())
    elif target.ssl_mode == "disabled":
        kwargs["ssl_disabled"] = True

    factory = (
        VerifiedConnection if target.ssl_mode == "verify-full" else pymysql.connect
    )
    connection = factory(**kwargs)
    try:
        if target.ssl_mode in {"required", "verify-full"} and not _tls_is_active(
            connection
        ):
            raise RuntimeError(
                "The server did not negotiate TLS while ssl_mode=required."
            )
        return connection
    except BaseException:
        connection.close()
        raise


def execute_mysql_query(target: Target, sql: str) -> QueryExecutionResult:
    connection = open_connection(target)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return fetch_limited_rows(cursor, target.max_rows or 200)
    finally:
        connection.close()


def _tls_is_active(connection: Any) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        row = cursor.fetchone()
    return bool(row and len(row) > 1 and row[1])
