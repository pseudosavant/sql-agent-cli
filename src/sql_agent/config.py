from __future__ import annotations

import configparser
import json
import os
import stat
import sys
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

import tomlkit

from .errors import ConfigurationError, UsageError
from .models import NETWORK_ENGINES, AppConfig, Defaults, Target
from .storage import atomic_write, locked, read_bytes

CONFIG_PATH = Path("~/.sql-agent-cli/config.toml").expanduser()
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


def config_path(path: Path | None = None) -> Path:
    return (
        (
            path
            or Path(os.getenv("SQL_AGENT_CLI_CONFIG") or "~/.sql-agent-cli/config.toml")
        )
        .expanduser()
        .absolute()
    )


def load_config(path: Path | None = None) -> AppConfig:
    path = config_path(path)
    original = read_bytes(path)
    try:
        raw = (
            tomllib.loads(original.decode("utf-8-sig")) if original is not None else {}
        )
        defaults = Defaults(
            **{f.name: raw.get("defaults", {}).get(f.name) for f in fields(Defaults)}
        )
        for item in fields(Defaults):
            value = getattr(defaults, item.name)
            if value is None:
                continue
            if item.name in {"target", "format"}:
                if not isinstance(value, str):
                    raise TypeError
            elif not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError
        if defaults.format and defaults.format not in {
            "json",
            "markdown",
            "table",
            "csv",
        }:
            raise ValueError
        validate_target(Target(name=defaults.target))
        targets = {}
        for name, values in raw.get("targets", {}).items():
            if not isinstance(values, dict):
                raise TypeError
            target = Target(
                name=name,
                **{
                    f.name: values[f.name]
                    for f in fields(Target)
                    if f.name not in {"name", "password"} and f.name in values
                },
            )
            target.engine = normalize_engine(target.engine)
            validate_target(target)
            targets[name] = target
        return AppConfig(defaults, targets, path, original)
    except (ValueError, TypeError, AttributeError):
        raise ConfigurationError(
            f"Invalid configuration. Check TOML syntax and target fields:\n  {path}\nRun uvx sql-agent-cli config --help. File contents were not displayed."
        ) from None


def save_config(config: AppConfig, path: Path | None = None) -> None:
    path = config_path(path or config.source_path)
    with locked(path):
        current = read_bytes(path)
        if config.source_path == path and current != config.source_bytes:
            raise ConfigurationError(
                "Configuration changed during setup. No replacement was made. Rerun setup."
            )
        try:
            document = (
                tomlkit.parse(current.decode("utf-8-sig"))
                if current
                else tomlkit.document()
            )
            defaults = document.setdefault("defaults", tomlkit.table())
            _update_table(
                defaults, config.defaults.to_dict(), {f.name for f in fields(Defaults)}
            )
            targets = document.setdefault("targets", tomlkit.table())
            for name in list(targets):
                if name not in config.targets:
                    del targets[name]
            for name, target in config.targets.items():
                validate_target(target)
                values = target.public_dict()
                values.pop("name", None)
                table = targets.setdefault(name, tomlkit.table())
                _update_table(
                    table,
                    values,
                    {f.name for f in fields(Target)} - {"password", "name"},
                )
            content = tomlkit.dumps(document).encode("utf-8")
        except (ValueError, TypeError, AttributeError):
            raise ConfigurationError(
                "Cannot update configuration. Check its TOML structure. Existing content was preserved."
            ) from None
        atomic_write(path, content, current)
        config.source_path, config.source_bytes = path, content


def _update_table(table, values: dict, known: set[str]) -> None:
    for key in known - values.keys():
        table.pop(key, None)
    for key, value in values.items():
        if table.get(key) != value:
            table[key] = value


def validate_target(target: Target) -> None:
    for item in fields(Target):
        value = getattr(target, item.name)
        if value is None or item.name == "password":
            continue
        if item.name in {
            "port",
            "max_rows",
            "connect_timeout_seconds",
            "query_timeout_seconds",
        }:
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise UsageError(f"{item.name} must be a positive integer.")
        elif not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise UsageError(
                f"Invalid {item.name}. Control characters are not allowed."
            )
    if target.port and target.port > 65535:
        raise UsageError("Port must be between 1 and 65535.")
    if target.engine and target.engine not in NETWORK_ENGINES | {"sqlite"}:
        raise UsageError(
            "Unsupported engine. Choose postgres, mysql, mariadb, or sqlite."
        )
    if target.ssl_mode and target.ssl_mode not in {
        "required",
        "preferred",
        "disabled",
        "verify-full",
    }:
        raise UsageError("Unsupported TLS mode. Run uvx sql-agent-cli --help.")
    if target.auth and target.auth not in {"auto", "native", "managed"}:
        raise UsageError("Authentication source must be auto, native, or managed.")
    for value in (target.host, target.database, target.user):
        if value and ("://" in value or "=" in value):
            raise UsageError(
                "Use individual connection fields or a password-free URL with setup. Connection strings are not accepted as field values."
            )


def resolve_target(config: AppConfig, args: Any) -> Target:
    requested_name = getattr(args, "target", None)
    explicit_engine = getattr(args, "engine", None)
    selected_name = requested_name or (
        None
        if explicit_engine
        else os.getenv("SQL_AGENT_CLI_TARGET") or config.defaults.target
    )
    base = Target(name=selected_name)
    if selected_name:
        try:
            base = config.targets[selected_name]
        except KeyError as exc:
            raise UsageError(
                "Unknown target. Run uvx sql-agent-cli targets --format json."
            ) from exc

    overlay = Target(
        name=base.name or "ephemeral",
        engine=normalize_engine(getattr(args, "engine", None)),
        database=getattr(args, "database", None),
        user=getattr(args, "user", None),
        host=getattr(args, "host", None),
        port=getattr(args, "port", None),
        path=getattr(args, "path", None),
        ssl_mode=getattr(args, "ssl_mode", None),
        ssl_ca=getattr(args, "ssl_ca", None),
        auth=getattr(args, "auth", None),
        auth_file=getattr(args, "auth_file", None),
        service=getattr(args, "service", None),
        unix_socket=getattr(args, "unix_socket", None),
        max_rows=getattr(args, "max_rows", None),
        connect_timeout_seconds=getattr(args, "connect_timeout_seconds", None),
        query_timeout_seconds=getattr(args, "query_timeout_seconds", None),
    )
    if getattr(args, "insecure", None):
        overlay.ssl_mode = args.insecure

    target = base.merged(overlay).with_defaults(config.defaults)
    target.name = target.name or "ephemeral"
    target.engine = normalize_engine(target.engine)
    validate_target(target)
    for key, value in native_connection_fields(target).items():
        if getattr(target, key) is None:
            setattr(target, key, value)
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
    validate_target(target)
    return target


def build_show_payload(config: AppConfig, path: Path | None = None) -> dict[str, Any]:
    path = config_path(path or config.source_path)
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
            "pgpass_candidates": [
                {"path": str(path), "exists": path.exists()}
                for path in postgres_auth_paths()
            ],
            "env_vars": {
                name: bool(os.getenv(name))
                for name in [
                    "PGHOST",
                    "PGPORT",
                    "PGDATABASE",
                    "PGUSER",
                    "PGPASSWORD",
                    "PGPASSFILE",
                    "PGSERVICE",
                    "PGSERVICEFILE",
                    "SQL_AGENT_CLI_PASSWORD",
                ]
            },
        }
    if engine in {"mysql", "mariadb"}:
        return {
            "option_file_candidates": [
                {"path": str(path), "exists": path.exists()}
                for path in (
                    [Path(target.auth_file).expanduser()]
                    if target.auth_file
                    else mysql_option_file_paths()
                )
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
    if os.getenv("PGPASSFILE"):
        return [Path(os.environ["PGPASSFILE"]).expanduser()]
    candidates: list[Path] = []
    appdata = os.getenv("APPDATA")
    if os.name == "nt" and appdata:
        candidates.append(Path(appdata) / "postgresql" / "pgpass.conf")
    else:
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


def find_mysql_option_file(target: Target | None = None) -> Path | None:
    if target and target.auth_file:
        return Path(target.auth_file).expanduser()
    for path in mysql_option_file_paths():
        if path.exists():
            return path
    return None


def native_connection_fields(target: Target) -> dict:
    """Read only a selected native source. Never return or display its password."""
    result = {}
    try:
        if target.engine == "postgres":
            service = target.service or os.getenv("PGSERVICE")
            if service:
                result["service"] = service
                default = (
                    Path(os.environ["APPDATA"]) / "postgresql" / ".pg_service.conf"
                    if os.name == "nt" and os.getenv("APPDATA")
                    else Path.home() / ".pg_service.conf"
                )
                paths = [Path(os.getenv("PGSERVICEFILE") or default)]
                if os.getenv("PGSYSCONFDIR"):
                    paths.append(Path(os.environ["PGSYSCONFDIR"]) / "pg_service.conf")
                for path in paths:
                    parser = configparser.RawConfigParser()
                    if path.exists():
                        with path.open(encoding="utf-8") as handle:
                            parser.read_file(handle)
                    if parser.has_section(service):
                        for key, option in {
                            "host": "host",
                            "port": "port",
                            "database": "dbname",
                            "user": "user",
                            "ssl_ca": "sslrootcert",
                        }.items():
                            if parser.has_option(service, option):
                                result[key] = parser.get(service, option)
                        break
            for key, variable in {
                "host": "PGHOST",
                "port": "PGPORT",
                "database": "PGDATABASE",
                "user": "PGUSER",
            }.items():
                if key not in result and os.getenv(variable):
                    result[key] = os.environ[variable]
        elif target.engine in {"mysql", "mariadb"}:
            path = find_mysql_option_file(target)
            if path:
                from pymysql.optionfile import Parser

                parser = Parser()
                with path.open(encoding="utf-8") as handle:
                    parser.read_file(handle)
                for key, option in {
                    "host": "host",
                    "port": "port",
                    "database": "database",
                    "user": "user",
                    "unix_socket": "socket",
                    "ssl_ca": "ssl-ca",
                }.items():
                    if parser.has_option("client", option):
                        result[key] = parser.get("client", option)
        if result.get("ssl_ca") == "system":
            result.pop("ssl_ca")
        if "port" in result:
            result["port"] = int(result["port"])
        return result
    except (OSError, ValueError, configparser.Error):
        raise ConfigurationError(
            "Cannot read the selected native authentication configuration. Check its path, access, and syntax. File contents were not displayed."
        ) from None


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
                    "# PostgreSQL password file for sql-agent-cli",
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
                    "# MySQL client option file for sql-agent-cli",
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
            bool(os.getenv(name))
            for name in ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
        )
    if engine in {"mysql", "mariadb"}:
        return find_mysql_option_file() is not None
    return False


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
