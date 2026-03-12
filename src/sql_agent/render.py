from __future__ import annotations

import base64
import csv
import json
from datetime import date, datetime, time
from decimal import Decimal
from io import StringIO
from uuid import UUID

from .models import QueryExecutionResult, Target, ValidatedQuery


def render_output(fmt: str, target: Target, query: ValidatedQuery, result: QueryExecutionResult) -> str:
    normalized = fmt.lower()
    if normalized == "json":
        return render_json(target, query, result)
    if normalized == "csv":
        return render_csv(result)
    if normalized == "markdown":
        return render_markdown(target, query, result)
    if normalized == "table":
        return render_table(target, query, result)
    raise ValueError(f"Unsupported format: {fmt}")


def render_json(target: Target, query: ValidatedQuery, result: QueryExecutionResult) -> str:
    payload = {
        "target": target.public_dict(),
        "query": {
            "input": query.input_text,
            "normalized": query.normalized_text,
            "statement_type": query.statement_type,
        },
        "result": {
            "columns": result.columns,
            "rows": [[serialize_value(value) for value in row] for row in result.rows],
            "returned_row_count": result.returned_row_count,
            "truncated": result.truncated,
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def render_csv(result: QueryExecutionResult) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow([display_value(value) for value in row])
    return buffer.getvalue()


def render_markdown(target: Target, query: ValidatedQuery, result: QueryExecutionResult) -> str:
    lines = [
        f"# Query Result ({target.name})",
        "",
        f"- engine: {target.engine}",
        f"- statement_type: {query.statement_type}",
        f"- returned_row_count: {result.returned_row_count}",
        f"- truncated: {str(result.truncated).lower()}",
        "",
        _render_grid(result.columns, result.rows),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_table(target: Target, query: ValidatedQuery, result: QueryExecutionResult) -> str:
    lines = [
        f"Target: {target.name} ({target.engine})",
        f"Statement: {query.statement_type}",
        f"Returned Rows: {result.returned_row_count}",
        f"Truncated: {str(result.truncated).lower()}",
        "",
        _render_grid(result.columns, result.rows),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_config_show_text(payload: dict[str, object]) -> str:
    defaults = payload.get("defaults", {})
    targets = payload.get("targets", {})
    default_target = defaults.get("target") if isinstance(defaults, dict) else None

    lines = [
        "Config",
        f"Path: {payload.get('config_path')}",
        f"Exists: {str(bool(payload.get('config_exists'))).lower()}",
        f"Default Target: {default_target or '-'}",
        "",
    ]

    if isinstance(defaults, dict):
        non_target_defaults = {k: v for k, v in defaults.items() if k != "target"}
        if non_target_defaults:
            lines.append("Defaults")
            for key in sorted(non_target_defaults):
                lines.append(f"- {key}: {non_target_defaults[key]}")
            lines.append("")

    if not isinstance(targets, dict) or not targets:
        lines.append("Targets")
        lines.append("(none)")
        return "\n".join(lines).rstrip() + "\n"

    rows: list[tuple[object, ...]] = []
    for name in sorted(targets):
        target = targets[name]
        if not isinstance(target, dict):
            continue
        rows.append(
            (
                "*" if name == default_target else "",
                name,
                target.get("engine", ""),
                _target_location(target),
                _target_identity(target),
                target.get("ssl_mode", ""),
                _credential_summary(target.get("credential_hints", {})),
                "yes" if target.get("can_attempt_connection") else "no",
            )
        )

    lines.append("Targets")
    lines.append(
        _render_grid(
            ["default", "name", "engine", "location", "identity", "ssl", "credentials", "ready"],
            rows,
        )
    )
    return "\n".join(lines).rstrip() + "\n"


def render_targets_text(payload: dict[str, object]) -> str:
    default_target = payload.get("default_target")
    targets = payload.get("targets", [])
    lines = ["Targets", f"Default Target: {default_target or '-'}", ""]
    if not isinstance(targets, list) or not targets:
        lines.append("(none)")
        return "\n".join(lines).rstrip() + "\n"

    rows: list[tuple[object, ...]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        name = target.get("name", "")
        rows.append(
            (
                "*" if name == default_target else "",
                name,
                target.get("engine", ""),
                _target_location(target),
                _target_identity(target),
                target.get("ssl_mode", ""),
            )
        )
    lines.append(_render_grid(["default", "name", "engine", "location", "identity", "ssl"], rows))
    return "\n".join(lines).rstrip() + "\n"


def serialize_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(value)).decode("ascii")
    return value


def display_value(value: object) -> str:
    serialized = serialize_value(value)
    if serialized is None:
        return ""
    return str(serialized)


def _render_grid(columns: list[str], rows: list[tuple[object, ...]]) -> str:
    prepared_rows = [[display_value(value) for value in row] for row in rows]
    widths = [len(column) for column in columns]
    for row in prepared_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def format_row(values: list[str]) -> str:
        return "| " + " | ".join(values[index].ljust(widths[index]) for index in range(len(values))) + " |"

    divider = "| " + " | ".join("-" * width for width in widths) + " |"
    lines = [format_row(columns), divider]
    lines.extend(format_row(row) for row in prepared_rows)
    return "\n".join(lines)


def _target_location(target: dict[str, object]) -> str:
    engine = str(target.get("engine", ""))
    if engine == "sqlite":
        return str(target.get("path", ""))
    host = str(target.get("host", ""))
    port = target.get("port")
    if host and port:
        return f"{host}:{port}"
    return host


def _target_identity(target: dict[str, object]) -> str:
    engine = str(target.get("engine", ""))
    if engine == "sqlite":
        return "-"
    database = str(target.get("database", ""))
    user = str(target.get("user", ""))
    if database and user:
        return f"{database} / {user}"
    return database or user or "-"


def _credential_summary(hints: object) -> str:
    if not isinstance(hints, dict):
        return "-"
    if "option_file_candidates" in hints:
        candidates = hints.get("option_file_candidates", [])
        if isinstance(candidates, list) and any(isinstance(item, dict) and item.get("exists") for item in candidates):
            return "option-file"
        return "missing option-file"
    if "pgpass_candidates" in hints:
        pgpass = hints.get("pgpass_candidates", [])
        env_vars = hints.get("env_vars", {})
        has_pgpass = isinstance(pgpass, list) and any(
            isinstance(item, dict) and item.get("exists") for item in pgpass
        )
        has_env = isinstance(env_vars, dict) and any(bool(value) for value in env_vars.values())
        if has_pgpass and has_env:
            return "pgpass + env"
        if has_pgpass:
            return "pgpass"
        if has_env:
            return "env"
        return "missing native auth"
    if "path_exists" in hints:
        return "path exists" if hints.get("path_exists") else "missing path"
    return "-"
