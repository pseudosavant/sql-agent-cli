from __future__ import annotations

from typing import Any

from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import ParseError

from .config import normalize_engine
from .models import ValidatedQuery


class SqlAgentError(Exception):
    exit_code = 1


class UsageError(SqlAgentError):
    exit_code = 2


class QueryValidationError(SqlAgentError):
    exit_code = 2


DISALLOWED_FUNCTIONS = {"SLEEP", "BENCHMARK", "LOAD_FILE"}
DISALLOWED_NODES = [
    "Alter",
    "Analyze",
    "Attach",
    "Call",
    "Command",
    "Commit",
    "Copy",
    "Create",
    "Delete",
    "Detach",
    "Drop",
    "Execute",
    "Grant",
    "Insert",
    "Kill",
    "Lock",
    "Merge",
    "Optimize",
    "Prepare",
    "Revoke",
    "Rollback",
    "Set",
    "Transaction",
    "TruncateTable",
    "Unlock",
    "Update",
    "Use",
    "Vacuum",
]


def get_query_text(args: Any, stdin_text: str | None) -> str:
    sources = [
        ("positional", getattr(args, "query_text", None)),
        ("flag", getattr(args, "query", None)),
        ("file", getattr(args, "sql_file", None)),
        ("stdin", stdin_text.strip() if stdin_text and stdin_text.strip() else None),
    ]
    present = [(name, value) for name, value in sources if value]
    if len(present) > 1:
        raise UsageError("Provide exactly one query source: positional SQL, --query, --sql-file, or stdin.")
    if not present:
        raise UsageError("No query provided. Pass a SQL string, --query, --sql-file, or stdin.")

    source_name, source_value = present[0]
    if source_name == "file":
        return _read_sql_file(str(source_value))
    return str(source_value)


def validate_query(sql: str, engine: str | None) -> ValidatedQuery:
    normalized = sql.strip()
    if not normalized:
        raise QueryValidationError("Empty query. Provide a single read-only SQL statement.")
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()

    try:
        parsed = parse(normalized, read=_dialect_for_engine(engine))
    except ParseError as exc:
        raise QueryValidationError(f"SQL parse error: {exc}") from exc

    if len(parsed) != 1:
        raise QueryValidationError("Exactly one SQL statement is allowed per invocation.")

    expression = parsed[0]
    if not _is_allowed_root(expression, engine):
        raise QueryValidationError("Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN statements are allowed.")

    _reject_disallowed_nodes(expression)
    _reject_disallowed_functions(expression)
    _reject_select_into(expression)

    return ValidatedQuery(
        input_text=sql,
        normalized_text=normalized,
        statement_type=expression.key.lower(),
    )


def _read_sql_file(path_text: str) -> str:
    try:
        return open(path_text, "r", encoding="utf-8").read()
    except OSError as exc:
        raise UsageError(f"Unable to read SQL file: {path_text}") from exc


def _dialect_for_engine(engine: str | None) -> str:
    normalized = normalize_engine(engine)
    if normalized in {"mysql", "mariadb"}:
        return "mysql"
    if normalized == "postgres":
        return "postgres"
    if normalized == "sqlite":
        return "sqlite"
    return ""


def _is_allowed_root(expression: exp.Expression, engine: str | None) -> bool:
    query_type = getattr(exp, "Query", None)
    describe_type = getattr(exp, "Describe", None)
    show_type = getattr(exp, "Show", None)
    explain_type = getattr(exp, "Explain", None)
    pragma_type = getattr(exp, "Pragma", None)

    if query_type and isinstance(expression, query_type):
        return True
    if describe_type and isinstance(expression, describe_type):
        return True
    if show_type and isinstance(expression, show_type):
        return True
    if explain_type and isinstance(expression, explain_type):
        return True
    if normalize_engine(engine) == "sqlite" and pragma_type and isinstance(expression, pragma_type):
        return True
    return False


def _reject_disallowed_nodes(expression: exp.Expression) -> None:
    for class_name in DISALLOWED_NODES:
        node_type = getattr(exp, class_name, None)
        if node_type and expression.find(node_type):
            raise QueryValidationError(f"Disallowed SQL operation detected: {class_name.lower()}.")


def _reject_disallowed_functions(expression: exp.Expression) -> None:
    for node in expression.walk():
        name = _function_name(node)
        if name and name.upper() in DISALLOWED_FUNCTIONS:
            raise QueryValidationError(f"Disallowed SQL function detected: {name}.")


def _function_name(node: exp.Expression) -> str | None:
    if hasattr(node, "sql_name") and callable(getattr(node, "sql_name")):
        try:
            return str(node.sql_name())
        except TypeError:
            pass
    if hasattr(node, "name"):
        name = getattr(node, "name")
        if isinstance(name, str) and name:
            return name
    return None


def _reject_select_into(expression: exp.Expression) -> None:
    into_type = getattr(exp, "Into", None)
    if into_type and expression.find(into_type):
        raise QueryValidationError("SELECT ... INTO style statements are not allowed.")
