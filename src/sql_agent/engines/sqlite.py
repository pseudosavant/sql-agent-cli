from __future__ import annotations

import sqlite3
from pathlib import Path
from time import monotonic
from urllib.parse import quote

from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows


def execute_sqlite_query(target: Target, sql: str) -> QueryExecutionResult:
    if not target.path:
        raise ValueError("SQLite target is missing --path.")

    resolved = Path(target.path).expanduser().resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro"
    connection = sqlite3.connect(
        uri, uri=True, timeout=target.connect_timeout_seconds or 8
    )
    deadline = (
        monotonic() + target.query_timeout_seconds
        if target.query_timeout_seconds
        else None
    )
    timed_out = False

    def stop_after_deadline() -> int:
        nonlocal timed_out
        timed_out = deadline is not None and monotonic() >= deadline
        return int(timed_out)

    if deadline is not None:
        connection.set_progress_handler(stop_after_deadline, 1_000)
    try:
        cursor = connection.cursor()
        try:
            try:
                cursor.execute(sql)
                return fetch_limited_rows(cursor, target.max_rows or 200)
            except sqlite3.DatabaseError as exc:
                if timed_out:
                    raise TimeoutError(
                        f"SQLite query exceeded the {target.query_timeout_seconds}-second timeout."
                    ) from exc
                raise
        finally:
            cursor.close()
    finally:
        if deadline is not None:
            connection.set_progress_handler(None, 0)
        connection.close()
