from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


NETWORK_ENGINES = {"mysql", "mariadb", "postgres"}


@dataclass
class Defaults:
    target: str | None = None
    format: str | None = None
    max_rows: int | None = None
    connect_timeout_seconds: int | None = None
    query_timeout_seconds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Target:
    name: str | None = None
    engine: str | None = None
    database: str | None = None
    user: str | None = None
    host: str | None = None
    port: int | None = None
    path: str | None = None
    ssl_mode: str | None = None
    max_rows: int | None = None
    connect_timeout_seconds: int | None = None
    query_timeout_seconds: int | None = None
    password: str | None = None

    def merged(self, overlay: "Target | None") -> "Target":
        if overlay is None:
            return Target(**asdict(self))
        merged: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(overlay, field.name)
            merged[field.name] = value if value is not None else getattr(self, field.name)
        return Target(**merged)

    def with_defaults(self, defaults: Defaults) -> "Target":
        copy = Target(**asdict(self))
        if copy.max_rows is None:
            copy.max_rows = defaults.max_rows
        if copy.connect_timeout_seconds is None:
            copy.connect_timeout_seconds = defaults.connect_timeout_seconds
        if copy.query_timeout_seconds is None:
            copy.query_timeout_seconds = defaults.query_timeout_seconds
        return copy

    def public_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "engine": self.engine,
            "database": self.database,
            "user": self.user,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "ssl_mode": self.ssl_mode,
            "max_rows": self.max_rows,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "query_timeout_seconds": self.query_timeout_seconds,
        }
        return {k: v for k, v in data.items() if v is not None}


@dataclass
class AppConfig:
    defaults: Defaults
    targets: dict[str, Target]


@dataclass
class ValidatedQuery:
    input_text: str
    normalized_text: str
    statement_type: str


@dataclass
class QueryExecutionResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    returned_row_count: int
    truncated: bool
