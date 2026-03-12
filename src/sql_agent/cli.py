from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .config import (
    build_show_payload,
    create_native_auth_template,
    default_port_for_engine,
    load_config,
    normalize_engine,
    resolve_target,
    save_config,
    serialize_show_payload,
)
from .models import AppConfig, Target
from .render import render_config_show_text, render_output, render_targets_text
from .validation import SqlAgentError, UsageError, get_query_text, validate_query

FORMAT_CHOICES = ("json", "markdown", "table", "csv")
ENGINE_CHOICES = ("mysql", "mariadb", "postgres", "postgresql", "sqlite")
SSL_MODE_CHOICES = ("required", "preferred", "disabled")
ADMIN_FORMAT_CHOICES = ("text", "json")


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        if args_list and args_list[0] == "config":
            return _handle_config(args_list[1:])
        if args_list and args_list[0] == "targets":
            return _handle_targets(args_list[1:])
        return _handle_query(args_list)
    except SqlAgentError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _handle_query(argv: list[str]) -> int:
    parser = _build_query_parser()
    args = parser.parse_args(argv)

    if args.password_stdin and args.prompt_password:
        raise UsageError("Choose at most one password source: --password-stdin or --prompt-password.")
    if args.ssl_mode and args.insecure:
        raise UsageError("Use either --ssl-mode or --insecure, not both.")

    stdin_text = None
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    config = load_config()
    target = resolve_target(config, args)
    if not target.engine:
        raise UsageError("No engine resolved. Configure a default target or provide --engine.")

    query_text = get_query_text(args, None if args.password_stdin else stdin_text)
    target.password = _resolve_runtime_password(args, stdin_text)
    validated = validate_query(query_text, target.engine)
    result = _execute_query(target, validated.normalized_text)

    output_format = args.format or config.defaults.format or "json"
    print(render_output(output_format, target, validated, result), end="")
    return 0


def _handle_config(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sql-agent config")
    subparsers = parser.add_subparsers(dest="command")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")

    set_default = subparsers.add_parser("set-default-target")
    set_default.add_argument("name")

    add_target = subparsers.add_parser("add-target")
    add_target.add_argument("name")
    _add_target_arguments(add_target)

    remove_target = subparsers.add_parser("remove-target")
    remove_target.add_argument("name")

    init_auth = subparsers.add_parser("init-native-auth")
    init_auth.add_argument("--engine", required=True, choices=("mysql", "mariadb", "postgres", "postgresql"))
    init_auth.add_argument("--target")

    args = parser.parse_args(argv)
    command = args.command or "show"
    config = load_config()

    if command == "show":
        payload = build_show_payload(config)
        if args.format == "json":
            print(serialize_show_payload(payload))
        else:
            print(render_config_show_text(payload), end="")
        return 0
    if command == "set-default-target":
        if args.name not in config.targets:
            raise UsageError(f"Unknown target: {args.name}")
        config.defaults.target = args.name
        save_config(config)
        print(json.dumps({"default_target": args.name}, indent=2))
        return 0
    if command == "add-target":
        return _config_add_target(config, args)
    if command == "remove-target":
        if args.name not in config.targets:
            raise UsageError(f"Unknown target: {args.name}")
        del config.targets[args.name]
        if config.defaults.target == args.name:
            config.defaults.target = None
        save_config(config)
        print(json.dumps({"removed_target": args.name}, indent=2))
        return 0
    if command == "init-native-auth":
        engine = normalize_engine(args.engine)
        target = None
        if args.target:
            if args.target not in config.targets:
                raise UsageError(f"Unknown target: {args.target}")
            target = config.targets[args.target]
        path = create_native_auth_template(engine, target)
        print(json.dumps({"created": str(path), "engine": engine}, indent=2))
        return 0

    raise UsageError("Unknown config command.")


def _handle_targets(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sql-agent targets")
    parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")
    args = parser.parse_args(argv)
    config = load_config()
    payload = {
        "default_target": config.defaults.target,
        "targets": [config.targets[name].public_dict() for name in sorted(config.targets)],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(render_targets_text(payload), end="")
    return 0


def _config_add_target(config: AppConfig, args: argparse.Namespace) -> int:
    existing = config.targets.get(args.name, Target(name=args.name))
    overlay = Target(
        name=args.name,
        engine=normalize_engine(args.engine),
        database=args.database,
        user=args.user,
        host=args.host,
        port=args.port,
        path=args.path,
        ssl_mode=args.ssl_mode,
        max_rows=args.max_rows,
        connect_timeout_seconds=args.connect_timeout_seconds,
        query_timeout_seconds=args.query_timeout_seconds,
    )
    target = existing.merged(overlay)
    if not target.engine:
        raise UsageError("config add-target requires --engine.")
    if target.engine in {"mysql", "mariadb", "postgres"} and target.port is None:
        target.port = default_port_for_engine(target.engine)
    config.targets[args.name] = target
    save_config(config)
    print(json.dumps({"target": target.public_dict()}, indent=2))
    return 0


def _add_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", choices=ENGINE_CHOICES)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--path")
    parser.add_argument("--ssl-mode", choices=SSL_MODE_CHOICES)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--connect-timeout-seconds", type=int)
    parser.add_argument("--query-timeout-seconds", type=int)


def _build_query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sql-agent")
    parser.add_argument("--target")
    parser.add_argument("--engine", choices=ENGINE_CHOICES)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--user")
    parser.add_argument("--path")
    parser.add_argument("--format", choices=FORMAT_CHOICES)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--connect-timeout-seconds", type=int)
    parser.add_argument("--query-timeout-seconds", type=int)
    parser.add_argument("--ssl-mode", choices=SSL_MODE_CHOICES)
    parser.add_argument("--insecure", action="store_const", const="preferred")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--prompt-password", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--sql-file")
    parser.add_argument("query_text", nargs="?")
    return parser


def _resolve_runtime_password(args: argparse.Namespace, stdin_text: str | None) -> str | None:
    if args.password_stdin:
        if stdin_text is not None and stdin_text.strip():
            return stdin_text.rstrip("\r\n")
        return sys.stdin.readline().rstrip("\r\n")
    if args.prompt_password:
        return getpass.getpass("Password: ")
    return None


def _execute_query(target: Target, sql: str):
    engine = normalize_engine(target.engine)
    if engine in {"mysql", "mariadb"}:
        from .engines.mysql import execute_mysql_query

        return execute_mysql_query(target, sql)
    if engine == "postgres":
        from .engines.postgres import execute_postgres_query

        return execute_postgres_query(target, sql)
    if engine == "sqlite":
        from .engines.sqlite import execute_sqlite_query

        return execute_sqlite_query(target, sql)
    raise UsageError(f"Unsupported engine: {target.engine}")
