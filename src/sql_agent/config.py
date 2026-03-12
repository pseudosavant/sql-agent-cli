from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

from .models import AppConfig, Defaults, NETWORK_ENGINES, Target

CONFIG_PATH = Path("~/.sql-agent/config.toml").expanduser()
DEFAULT_FORMAT = "json"
DEFAULT_MAX_ROWS = 200
DEFAULT_CONNECT_TIMEOUT_SECONDS = 8
DEFAULT_QUERY_TIMEOUT_SECONDS = 15


def normalize_engine(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "postgresql":
        return "postgres"
    return normalized


def default_port_for_engine(engine: str | None) -> int | None:
    if engine in {"mysql", "mariadb"}:
        return 3306
    if engine == "postgres":
        return 5432
    return None


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    if not path.exists():
        return AppConfig(defaults=Defaults(), targets={})

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    defaults_raw = raw.get("defaults", {})
    defaults = Defaults(
        target=defaults_raw.get("target"),
        format=defaults_raw.get("format"),
        max_rows=_int_or_none(defaults_raw.get("max_rows")),
        connect_timeout_seconds=_int_or_none(defaults_raw.get("connect_timeout_seconds")),
        query_timeout_seconds=_int_or_none(defaults_raw.get("query_timeout_seconds")),
    )

    targets: dict[str, Target] = {}
    for name, target_raw in raw.get("targets", {}).items():
        targets[name] = Target(
            name=name,
            engine=normalize_engine(target_raw.get("engine")),
            database=target_raw.get("database"),
            user=target_raw.get("user"),
            host=target_raw.get("host"),
            port=_int_or_none(target_raw.get("port")),
            path=target_raw.get("path"),
            ssl_mode=target_raw.get("ssl_mode"),
            max_rows=_int_or_none(target_raw.get("max_rows")),
            connect_timeout_seconds=_int_or_none(target_raw.get("connect_timeout_seconds")),
            query_timeout_seconds=_int_or_none(target_raw.get("query_timeout_seconds")),
        )
    return AppConfig(defaults=defaults, targets=targets)


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(config), encoding="utf-8")


def resolve_target(config: AppConfig, args: Any) -> Target:
    selected_name = getattr(args, "target", None) or config.defaults.target
    base = Target(name=selected_name)
    if selected_name:
        try:
            base = config.targets[selected_name]
        except KeyError as exc:
            raise ValueError(f"Unknown target: {selected_name}") from exc

    overlay = Target(
        name=base.name or "ephemeral",
        engine=normalize_engine(getattr(args, "engine", None)),
        database=getattr(args, "database", None),
        user=getattr(args, "user", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        path=getattr(args, "path", None),
        ssl_mode=getattr(args, "ssl_mode", None),
        max_rows=getattr(args, "max_rows", None),
        connect_timeout_seconds=getattr(args, "connect_timeout_seconds", None),
        query_timeout_seconds=getattr(args, "query_timeout_seconds", None),
    )
    if getattr(args, "insecure", None):
        overlay.ssl_mode = getattr(args, "insecure")

    target = base.merged(overlay).with_defaults(config.defaults)
    target.name = target.name or "ephemeral"
    target.engine = normalize_engine(target.engine)
    if target.engine in NETWORK_ENGINES and target.port is None:
        target.port = default_port_for_engine(target.engine)
    if target.engine in NETWORK_ENGINES and target.ssl_mode is None:
        target.ssl_mode = "required"
    if target.max_rows is None:
        target.max_rows = DEFAULT_MAX_ROWS
    if target.connect_timeout_seconds is None:
        target.connect_timeout_seconds = DEFAULT_CONNECT_TIMEOUT_SECONDS
    if target.query_timeout_seconds is None:
        target.query_timeout_seconds = DEFAULT_QUERY_TIMEOUT_SECONDS
    return target


def build_show_payload(config: AppConfig, path: Path = CONFIG_PATH) -> dict[str, Any]:
    return {
        "config_path": str(path),
        "config_exists": path.exists(),
        "defaults": config.defaults.to_dict(),
        "targets": {
            name: {
                **target.public_dict(),
                "credential_hints": credential_hints_for_target(target),
                "can_attempt_connection": can_attempt_connection(target),
            }
            for name, target in sorted(config.targets.items())
        },
    }


def credential_hints_for_target(target: Target) -> dict[str, Any]:
    engine = normalize_engine(target.engine)
    if engine == "postgres":
        return {
            "pgpass_candidates": [{"path": str(path), "exists": path.exists()} for path in postgres_auth_paths()],
            "env_vars": {
                name: bool(os.getenv(name))
                for name in ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
            },
        }
    if engine in {"mysql", "mariadb"}:
        return {
            "option_file_candidates": [
                {"path": str(path), "exists": path.exists()} for path in mysql_option_file_paths()
            ]
        }
    if engine == "sqlite":
        exists = Path(target.path).expanduser().exists() if target.path else False
        return {"path_exists": exists}
    return {}


def can_attempt_connection(target: Target) -> bool:
    engine = normalize_engine(target.engine)
    if engine == "sqlite":
        return bool(target.path)
    if engine in NETWORK_ENGINES:
        return bool(target.engine and (target.host or _native_auth_available(engine)))
    return False


def postgres_auth_paths() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "postgresql" / "pgpass.conf")
    candidates.append(Path("~/.pgpass").expanduser())
    return _unique_paths(candidates)


def preferred_postgres_auth_path() -> Path:
    return postgres_auth_paths()[0]


def mysql_option_file_paths() -> list[Path]:
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "MySQL" / ".my.cnf")
    candidates.append(Path("~/.my.cnf").expanduser())
    return _unique_paths(candidates)


def preferred_mysql_option_file_path() -> Path:
    return mysql_option_file_paths()[0]


def find_mysql_option_file() -> Path | None:
    for path in mysql_option_file_paths():
        if path.exists():
            return path
    return None


def create_native_auth_template(engine: str, target: Target | None) -> Path:
    path, content = _native_auth_template(engine, target)
    if path.exists():
        raise FileExistsError(f"Native auth file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    if normalize_engine(engine) == "postgres":
        _try_restrict_permissions(path)
    return path


def serialize_show_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _native_auth_template(engine: str, target: Target | None) -> tuple[Path, str]:
    normalized = normalize_engine(engine)
    if normalized == "postgres":
        host = target.host if target else "hostname"
        port = str(target.port) if target and target.port else "5432"
        database = target.database if target else "database"
        user = target.user if target else "username"
        return (
            preferred_postgres_auth_path(),
            "\n".join(
                [
                    "# PostgreSQL password file for sql-agent",
                    "# Format: hostname:port:database:username:password",
                    "# Use restrictive file permissions where PostgreSQL requires them.",
                    f"{host}:{port}:{database}:{user}:",
                    "",
                ]
            ),
        )
    if normalized in {"mysql", "mariadb"}:
        host = target.host if target else "hostname"
        port = str(target.port) if target and target.port else "3306"
        user = target.user if target else "username"
        return (
            preferred_mysql_option_file_path(),
            "\n".join(
                [
                    "# MySQL client option file for sql-agent",
                    "# Stores plaintext credentials. Protect it with filesystem permissions.",
                    "[client]",
                    f"host={host}",
                    f"port={port}",
                    f"user={user}",
                    "password=",
                    "",
                ]
            ),
        )
    raise ValueError(f"Native auth templates are not supported for engine: {engine}")


def _native_auth_available(engine: str) -> bool:
    if engine == "postgres":
        return any(path.exists() for path in postgres_auth_paths()) or any(
            bool(os.getenv(name)) for name in ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
        )
    if engine in {"mysql", "mariadb"}:
        return find_mysql_option_file() is not None
    return False


def _dump_toml(config: AppConfig) -> str:
    lines: list[str] = []
    lines.append("[defaults]")
    defaults = config.defaults.to_dict()
    for key in ["target", "format", "max_rows", "connect_timeout_seconds", "query_timeout_seconds"]:
        if key in defaults:
            lines.append(f"{key} = {_toml_value(defaults[key])}")
    if not defaults:
        lines.append("# Configure default target and output options here.")
    lines.append("")

    for name in sorted(config.targets):
        target = config.targets[name]
        lines.append(f"[targets.{name}]")
        public = target.public_dict()
        public.pop("name", None)
        for key in [
            "engine",
            "host",
            "port",
            "database",
            "user",
            "path",
            "ssl_mode",
            "max_rows",
            "connect_timeout_seconds",
            "query_timeout_seconds",
        ]:
            if key in public:
                lines.append(f"{key} = {_toml_value(public[key])}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _try_restrict_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        print(f"Warning: unable to restrict permissions on {path}", file=sys.stderr)


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique
