from __future__ import annotations

from typing import Any

from sqlglot import expressions as exp
from sqlglot import parse
from sqlglot.errors import ParseError

from .config import normalize_engine
from .errors import QueryExecutionError, QueryValidationError, SqlAgentError, UsageError
from .models import ValidatedQuery

__all__ = [
    "QueryExecutionError",
    "QueryValidationError",
    "SqlAgentError",
    "UsageError",
    "get_query_text",
    "validate_query",
]

DISALLOWED_FUNCTIONS = {
    "BENCHMARK",
    "GET_LOCK",
    "LOAD_FILE",
    "LO_EXPORT",
    "LO_IMPORT",
    "PG_LS_DIR",
    "PG_READ_BINARY_FILE",
    "PG_READ_FILE",
    "PG_STAT_FILE",
    "RELEASE_ALL_LOCKS",
    "RELEASE_LOCK",
    "SLEEP",
}
DISALLOWED_FUNCTION_PREFIXES = (
    "PG_ADVISORY_LOCK",
    "PG_ADVISORY_UNLOCK",
    "PG_ADVISORY_XACT_LOCK",
    "PG_TRY_ADVISORY_LOCK",
    "PG_TRY_ADVISORY_XACT_LOCK",
)
READ_ONLY_SQLITE_PRAGMAS = {
    "application_id",
    "collation_list",
    "compile_options",
    "database_list",
    "encoding",
    "foreign_key_check",
    "foreign_key_list",
    "foreign_keys",
    "freelist_count",
    "function_list",
    "index_info",
    "index_list",
    "index_xinfo",
    "integrity_check",
    "module_list",
    "page_count",
    "pragma_list",
    "quick_check",
    "schema_version",
    "table_info",
    "table_list",
    "table_xinfo",
    "user_version",
}
SQLITE_PRAGMAS_WITH_READ_ARGUMENTS = {
    "foreign_key_check",
    "foreign_key_list",
    "index_info",
    "index_list",
    "index_xinfo",
    "integrity_check",
    "quick_check",
    "table_info",
    "table_xinfo",
}
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
        raise UsageError(
            "Provide exactly one query source: positional SQL, --query, --sql-file, or stdin."
        )
    if not present:
        raise UsageError(
            "No query provided. Pass a SQL string, --query, --sql-file, or stdin."
        )

    source_name, source_value = present[0]
    if source_name == "file":
        return _read_sql_file(str(source_value))
    return str(source_value)


def validate_query(sql: str, engine: str | None) -> ValidatedQuery:
    normalized = sql.strip()
    if not normalized:
        raise QueryValidationError(
            "Empty query. Provide a single read-only SQL statement."
        )
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()

    try:
        parsed = parse(normalized, read=_dialect_for_engine(engine))
    except ParseError as exc:
        raise QueryValidationError(f"SQL parse error: {exc}") from exc

    if len(parsed) != 1:
        raise QueryValidationError(
            "Exactly one SQL statement is allowed per invocation."
        )

    expression = parsed[0]
    if not _is_allowed_root(expression, engine):
        raise QueryValidationError(
            "Only read-only SELECT/SHOW/DESCRIBE/EXPLAIN statements are allowed."
        )

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
    if (
        normalize_engine(engine) == "sqlite"
        and pragma_type
        and isinstance(expression, pragma_type)
    ):
        _validate_sqlite_pragma(expression)
        return True
    return False


def _validate_sqlite_pragma(expression: exp.Expression) -> None:
    pragma = expression.this
    if isinstance(pragma, exp.EQ):
        # Newer SQLGlot versions represent argument pragmas as equality nodes.
        # These metadata pragmas accept a read argument in either SQLite syntax.
        name = _function_name(pragma.this)
        if name and name.lower() in SQLITE_PRAGMAS_WITH_READ_ARGUMENTS:
            return
        raise QueryValidationError(
            "Assignments to SQLite PRAGMA values are not allowed."
        )

    name = _function_name(pragma)
    if not name or name.lower() not in READ_ONLY_SQLITE_PRAGMAS:
        raise QueryValidationError(
            "Only explicitly allowlisted read-only SQLite PRAGMA statements are allowed."
        )
    if pragma.expressions and name.lower() not in SQLITE_PRAGMAS_WITH_READ_ARGUMENTS:
        raise QueryValidationError(
            "Assignments to SQLite PRAGMA values are not allowed."
        )


def _reject_disallowed_nodes(expression: exp.Expression) -> None:
    for class_name in DISALLOWED_NODES:
        node_type = getattr(exp, class_name, None)
        if node_type and expression.find(node_type):
            raise QueryValidationError(
                f"Disallowed SQL operation detected: {class_name.lower()}."
            )


def _reject_disallowed_functions(expression: exp.Expression) -> None:
    for node in expression.walk():
        name = _function_name(node)
        normalized = name.upper() if name else ""
        if normalized in DISALLOWED_FUNCTIONS or normalized.startswith(
            DISALLOWED_FUNCTION_PREFIXES
        ):
            raise QueryValidationError(f"Disallowed SQL function detected: {name}.")


def _function_name(node: exp.Expression) -> str | None:
    if hasattr(node, "name"):
        name = node.name
        if isinstance(name, str) and name:
            return name
    if hasattr(node, "sql_name") and callable(node.sql_name):
        try:
            return str(node.sql_name())
        except TypeError:
            pass
    return None


def _reject_select_into(expression: exp.Expression) -> None:
    into_type = getattr(exp, "Into", None)
    if into_type and expression.find(into_type):
        raise QueryValidationError("SELECT ... INTO style statements are not allowed.")
