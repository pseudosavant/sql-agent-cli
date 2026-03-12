from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from ..models import QueryExecutionResult, Target
from .base import fetch_limited_rows


def execute_sqlite_query(target: Target, sql: str) -> QueryExecutionResult:
    if not target.path:
        raise ValueError("SQLite target is missing --path.")

    resolved = Path(target.path).expanduser().resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=target.connect_timeout_seconds or 8)
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(sql)
            return fetch_limited_rows(cursor, target.max_rows or 200)
        finally:
            cursor.close()
    finally:
        connection.close()
