from __future__ import annotations

import re


class SqlAgentError(Exception):
    exit_code = 1
    code = "runtime_error"


class UsageError(SqlAgentError):
    exit_code = 2
    code = "invalid_input"


class QueryValidationError(UsageError):
    pass


class QueryExecutionError(SqlAgentError):
    pass


class ConfigurationError(SqlAgentError):
    code = "configuration_unavailable"


class CredentialError(SqlAgentError):
    code = "credential_unavailable"


def setup_command(name: str | None = None, *, replace: bool = False) -> str:
    command = "uvx sql-agent-cli setup"
    if (
        name
        and name != "ephemeral"
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
    ):
        command += f" --target {name}"
    if replace:
        command += " --replace-password"
    return command


def diagnostic(exc: Exception, name: str | None = None) -> dict:
    """Classify driver failures without returning their potentially secret text."""
    recovery = setup_command(name)
    if isinstance(exc, SqlAgentError):
        return {
            "type": type(exc).__name__,
            "code": exc.code,
            "message": str(exc),
            "next_steps": [recovery],
        }
    state = getattr(exc, "sqlstate", None) or ""
    number = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    text = str(exc).lower()
    if number in {1820, 1862} or "password has expired" in text:
        code, message = (
            "credential_expired",
            "The database password has expired. Renew it through your database administrator or native client.",
        )
        recovery = setup_command(name, replace=True)
    elif (
        state.startswith("28")
        or number in {1045, 1698}
        or any(
            word in text
            for word in (
                "authentication failed",
                "password authentication",
                "no password supplied",
                "access denied for user",
            )
        )
    ):
        code, message = (
            "authentication_failed",
            "Database authentication failed. Confirm the login and credential source. Enter credentials only in your terminal.",
        )
        recovery = setup_command(name, replace=True)
    elif state == "42501" or number in {1044, 1142, 1143}:
        code, message = (
            "permission_denied",
            "The database denied access. Ask its owner for the required schema and SELECT privileges, then retry the query.",
        )
    elif state == "3D000" or number == 1049:
        code, message = (
            "database_not_found",
            "The selected database does not exist or is unavailable. Confirm its name with the database administrator.",
        )
    elif number == 2026 or any(word in text for word in ("certificate", "ssl", "tls")):
        code, message = (
            "tls_verification_failed",
            "TLS verification failed. Confirm the hostname, server TLS configuration, and trusted CA file. Do not weaken TLS automatically.",
        )
    elif "not a database" in text or "malformed" in text:
        code, message = (
            "invalid_database",
            "The file could not be read as a valid SQLite database. Select an existing database or a valid snapshot.",
        )
    elif "unable to open database" in text:
        code, message = (
            "database_unavailable",
            "The SQLite file is unavailable in this execution environment. Confirm its absolute path and file access. No database was created.",
        )
    elif (
        isinstance(exc, TimeoutError)
        or "timeout" in text
        or "timed out" in text
        or state == "57014"
    ):
        code, message = (
            "timeout",
            "The database operation timed out. Check the endpoint, network route, and query limits.",
        )
    elif (
        state.startswith("08")
        or number in {2002, 2003, 2005, 2006, 2013}
        or any(word in text for word in ("connection", "resolve", "host name"))
    ):
        code, message = (
            "connection_failed",
            "The database could not be reached. Check its endpoint, DNS, VPN, firewall, and this execution environment's network access.",
        )
    else:
        code, message = (
            "query_failed",
            "The database operation failed. Check the selected target, SQL, and database permissions.",
        )
    return {
        "type": type(exc).__name__,
        "code": code,
        "message": message,
        "next_steps": [recovery],
    }


def credential_recovery(error: dict, source: str | None, name: str | None) -> dict:
    if error.get("code") not in {"authentication_failed", "credential_expired"}:
        return error
    error = dict(error)
    if source in {"SQL_AGENT_CLI_PASSWORD", "PGPASSWORD", "native"}:
        command = setup_command(name).replace(" setup", " config check", 1)
        instruction = (
            "Renew or correct the native credential in this execution environment."
            if source == "native"
            else f"Renew or replace {source} in this execution environment. Setup does not save or refresh it."
        )
        error["message"] += " " + instruction
        error["next_steps"] = [command]
    return error
