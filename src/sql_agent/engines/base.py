from __future__ import annotations

from typing import Any

from ..models import QueryExecutionResult


def fetch_limited_rows(cursor: Any, max_rows: int) -> QueryExecutionResult:
    columns = [column[0] for column in (cursor.description or [])]
    if not columns:
        return QueryExecutionResult(columns=[], rows=[], returned_row_count=0, truncated=False)

    rows = list(cursor.fetchmany(max_rows + 1))
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]

    return QueryExecutionResult(
        columns=columns,
        rows=[tuple(row) for row in rows],
        returned_row_count=len(rows),
        truncated=truncated,
    )
