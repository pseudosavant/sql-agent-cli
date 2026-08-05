from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
from pathlib import Path

from . import __version__
from .config import (
    build_show_payload,
    can_attempt_connection,
    create_native_auth_template,
    credential_hints_for_target,
    default_port_for_engine,
    load_config,
    normalize_engine,
    resolve_target,
    save_config,
    serialize_show_payload,
)
from .models import AppConfig, Target
from .render import (
    render_config_check_text,
    render_config_show_text,
    render_output,
    render_targets_text,
)
from .skill import install_skill, remove_skill
from .validation import (
    QueryExecutionError,
    SqlAgentError,
    UsageError,
    get_query_text,
    validate_query,
)

FORMAT_CHOICES = ("json", "markdown", "table", "csv")
ENGINE_CHOICES = ("mysql", "mariadb", "postgres", "postgresql", "sqlite")
SSL_MODE_CHOICES = ("required", "preferred", "disabled")
ADMIN_FORMAT_CHOICES = ("text", "json")
PROGRAM_NAME = "sql-agent-cli"
PROJECT_URL = "https://github.com/pseudosavant/sql-agent-cli"
PROJECT_SUMMARY = "Read-only SQL CLI for agentic workflows"
PROJECT_LICENSE = "MIT"


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        if args_list and args_list[0] == "config":
            return _handle_config(args_list[1:])
        if args_list and args_list[0] == "targets":
            return _handle_targets(args_list[1:])
        if args_list and args_list[0] == "install-skill":
            return _handle_install_skill(args_list[1:])
        if args_list and args_list[0] == "remove-skill":
            return _handle_remove_skill(args_list[1:])
        return _handle_query(args_list)
    except SqlAgentError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI commands must never expose driver tracebacks.
        message = " ".join(str(exc).split()) or type(exc).__name__
        message = re.sub(r"(?i)(password\s*=\s*)[^\s,;]+", r"\1<redacted>", message)
        print(f"Unexpected runtime failure: {message}", file=sys.stderr)
        return 1


def _handle_query(argv: list[str]) -> int:
    parser = _build_query_parser()
    if not argv:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)

    if args.about:
        print(
            "\n".join(
                [
                    f"{PROGRAM_NAME} {__version__}",
                    PROJECT_SUMMARY,
                    f"Project: {PROJECT_URL}",
                    f"License: {PROJECT_LICENSE}",
                ]
            )
        )
        return 0

    if args.password_stdin and args.prompt_password:
        raise UsageError(
            "Choose at most one password source: --password-stdin or --prompt-password."
        )
    if args.ssl_mode and args.insecure:
        raise UsageError("Use either --ssl-mode or --insecure, not both.")

    stdin_text = None
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    config = load_config()
    target = resolve_target(config, args)
    if not target.engine:
        raise UsageError(
            "No engine resolved. Configure a default target or provide --engine."
        )

    query_text = get_query_text(args, None if args.password_stdin else stdin_text)
    target.password = _resolve_runtime_password(args, stdin_text)
    validated = validate_query(query_text, target.engine)
    result = _execute_query(target, validated.normalized_text)

    output_format = args.format or config.defaults.format or "json"
    print(render_output(output_format, target, validated, result), end="")
    return 0


def _handle_config(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{PROGRAM_NAME} config",
        description=(
            "Inspect or update sql-agent-cli configuration.\n\n"
            "Most query runs should not start here. The normal workflow is:\n"
            f'  {PROGRAM_NAME} "SELECT ..."\n\n'
            "Use config commands when you need to inspect targets, change the default\n"
            "target, or bootstrap native auth files."
        ),
        epilog=(
            "Examples:\n"
            f"  {PROGRAM_NAME} config show\n"
            f"  {PROGRAM_NAME} config check\n"
            f"  {PROGRAM_NAME} config set-default-target dev\n"
            f"  {PROGRAM_NAME} config add-target reporting --engine postgres --host db.example.com --database app --user reader\n"
            f"  {PROGRAM_NAME} config init-native-auth --engine postgres --target reporting"
        ),
        formatter_class=HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")

    check_parser = subparsers.add_parser(
        "check",
        help="Safely test target connectivity with SELECT 1.",
        description=(
            "Safely test configured target connectivity with an internal SELECT 1.\n\n"
            "The default target is checked unless --target or --all is supplied.\n"
            "Output includes non-secret target, credential-source, and connection status."
        ),
        formatter_class=HelpFormatter,
    )
    check_selection = check_parser.add_mutually_exclusive_group()
    check_selection.add_argument(
        "--target", metavar="NAME", help="Check this configured target."
    )
    check_selection.add_argument(
        "--all",
        action="store_true",
        help="Check every configured target instead of only the default target.",
    )
    check_parser.add_argument(
        "--format",
        choices=ADMIN_FORMAT_CHOICES,
        default="text",
        help="Diagnostic output format. Defaults to text.",
    )

    set_default = subparsers.add_parser("set-default-target")
    set_default.add_argument("name")

    add_target = subparsers.add_parser("add-target")
    add_target.add_argument("name")
    _add_target_arguments(add_target)

    remove_target = subparsers.add_parser("remove-target")
    remove_target.add_argument("name")

    init_auth = subparsers.add_parser("init-native-auth")
    init_auth.add_argument(
        "--engine",
        required=True,
        choices=("mysql", "mariadb", "postgres", "postgresql"),
    )
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
    if command == "check":
        return _config_check(config, args)
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
    parser = argparse.ArgumentParser(
        prog=f"{PROGRAM_NAME} targets",
        description=(
            "List configured targets and show which one is the default.\n\n"
            "This is a quick inspection command. Query execution still happens through:\n"
            f'  {PROGRAM_NAME} "SELECT ..."'
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")
    args = parser.parse_args(argv)
    config = load_config()
    payload = {
        "default_target": config.defaults.target,
        "targets": [
            config.targets[name].public_dict() for name in sorted(config.targets)
        ],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(render_targets_text(payload), end="")
    return 0


def _handle_install_skill(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{PROGRAM_NAME} install-skill",
        description="Install or update the $sql-agent-cli skill.",
    )
    parser.add_argument(
        "--skills-dir", type=Path, help="Install into this skills root directory."
    )
    args = parser.parse_args(argv)
    print(json.dumps(install_skill(args.skills_dir), indent=2))
    return 0


def _handle_remove_skill(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{PROGRAM_NAME} remove-skill",
        description="Remove the managed $sql-agent-cli skill.",
    )
    parser.add_argument(
        "--skills-dir", type=Path, help="Remove from this skills root directory."
    )
    parser.add_argument(
        "--force", action="store_true", help="Remove even if the skill is not managed."
    )
    args = parser.parse_args(argv)
    print(json.dumps(remove_skill(args.skills_dir, force=args.force), indent=2))
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


def _config_check(config: AppConfig, args: argparse.Namespace) -> int:
    if args.all:
        names = sorted(config.targets)
        if not names:
            raise UsageError("No configured targets to check.")
    else:
        name = args.target or config.defaults.target
        if not name:
            raise UsageError(
                "No target selected. Configure a default target, use --target NAME, or use --all."
            )
        if name not in config.targets:
            raise UsageError(f"Unknown target: {name}")
        names = [name]

    results: list[dict[str, object]] = []
    for name in names:
        target = resolve_target(config, argparse.Namespace(target=name))
        item: dict[str, object] = {
            "target": target.public_dict(),
            "credential_hints": credential_hints_for_target(target),
            "can_attempt_connection": can_attempt_connection(target),
        }
        try:
            if not target.engine:
                raise ValueError("Target is missing an engine.")
            check_result = _execute_query(target, "SELECT 1 AS sql_agent_cli_check")
            if not check_result.rows or check_result.rows[0][0] != 1:
                raise RuntimeError("Connection check returned an unexpected result.")
            item["status"] = "ok"
        except Exception as exc:  # noqa: BLE001 - check --all must report every driver failure.
            item["status"] = "error"
            item["error"] = {
                "type": type(exc).__name__,
                "message": _safe_diagnostic_message(exc, target),
            }
        results.append(item)

    failed = sum(1 for item in results if item["status"] == "error")
    payload = {
        "checked": len(results),
        "succeeded": len(results) - failed,
        "failed": failed,
        "results": results,
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_config_check_text(payload), end="")
    return 1 if failed else 0


def _safe_diagnostic_message(exc: Exception, target: Target) -> str:
    message = " ".join(str(exc).split())
    if target.password:
        message = message.replace(target.password, "<redacted>")
    return re.sub(r"(?i)(password\s*=\s*)[^\s,;]+", r"\1<redacted>", message)


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
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        usage=(
            f'{PROGRAM_NAME} "SELECT ..."\n'
            f'  {PROGRAM_NAME} --target NAME "SELECT ..."\n'
            f'  {PROGRAM_NAME} [connection options] "SELECT ..."\n'
            f"  {PROGRAM_NAME} config --help"
        ),
        description=(
            "Run one read-only SQL query and print deterministic output.\n\n"
            "Happy path:\n"
            f'  {PROGRAM_NAME} "SELECT ..."\n\n'
            "By default, sql-agent-cli uses the configured default target/environment.\n"
            "If a default target is already configured, the usual invocation is to pass\n"
            "only the SQL query.\n"
            "Do not search for config files or environment details first unless the query\n"
            "fails or you need a non-default target."
        ),
        epilog=(
            "Target resolution:\n"
            "  1. --target NAME\n"
            "  2. An explicit --engine builds an ephemeral target\n"
            "  3. [defaults].target from ~/.sql-agent-cli/config.toml\n\n"
            "Query sources:\n"
            "  Provide exactly one of: positional SQL, --query, --sql-file, or stdin.\n\n"
            "Examples:\n"
            f'  {PROGRAM_NAME} "SELECT COUNT(*) AS total FROM users"\n'
            f'  {PROGRAM_NAME} --target reporting "SELECT NOW()"\n'
            f'  {PROGRAM_NAME} --query "SELECT id, name FROM users ORDER BY id LIMIT 10"\n'
            f"  {PROGRAM_NAME} --sql-file query.sql\n"
            f"  Get-Content query.sql | {PROGRAM_NAME}\n"
            f'  {PROGRAM_NAME} --engine sqlite --path C:\\data\\app.db "SELECT * FROM customers LIMIT 5"\n\n'
            "Admin commands:\n"
            f"  {PROGRAM_NAME} targets\n"
            f"  {PROGRAM_NAME} config show\n"
            f"  {PROGRAM_NAME} config check\n"
            f"  {PROGRAM_NAME} config set-default-target NAME\n"
            f"  {PROGRAM_NAME} install-skill\n"
            f"  {PROGRAM_NAME} remove-skill"
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--about",
        action="store_true",
        help="Show version, project, and license information and exit.",
    )

    target_group = parser.add_argument_group("Target and environment")
    target_group.add_argument(
        "--target",
        metavar="NAME",
        help="Use a named target. If omitted, the configured default target is used.",
    )
    target_group.add_argument(
        "--engine",
        choices=ENGINE_CHOICES,
        help="Build an ephemeral one-off target. Usually unnecessary when a default target is configured.",
    )
    target_group.add_argument(
        "--host", help="Override or define the database host for this run."
    )
    target_group.add_argument(
        "--port", type=int, help="Override or define the database port for this run."
    )
    target_group.add_argument(
        "--database", help="Override or define the database name for this run."
    )
    target_group.add_argument(
        "--user", help="Override or define the database user for this run."
    )
    target_group.add_argument("--path", help="SQLite database path for this run.")

    query_group = parser.add_argument_group("Query input")
    query_group.add_argument("--query", metavar="SQL", help="SQL query text.")
    query_group.add_argument(
        "--sql-file", metavar="PATH", help="Read SQL query text from a file."
    )
    query_group.add_argument(
        "query_text",
        nargs="?",
        metavar="SQL",
        help='SQL query text. This is the normal happy path: sql-agent-cli "SELECT ..."',
    )

    output_group = parser.add_argument_group("Output and guardrails")
    output_group.add_argument(
        "--format",
        choices=FORMAT_CHOICES,
        help="Output format. Defaults to config, then json.",
    )
    output_group.add_argument(
        "--max-rows", type=int, help="Maximum rows to emit before truncating output."
    )
    output_group.add_argument(
        "--connect-timeout-seconds",
        type=int,
        help="Connection timeout override for this run.",
    )
    output_group.add_argument(
        "--query-timeout-seconds",
        type=int,
        help="Query timeout override for this run.",
    )
    output_group.add_argument(
        "--ssl-mode",
        choices=SSL_MODE_CHOICES,
        help=(
            "TLS behavior for network databases. 'required' requires encryption; "
            "certificate verification depends on native client configuration."
        ),
    )
    output_group.add_argument(
        "--insecure",
        action="store_const",
        const="preferred",
        help="Shorthand for --ssl-mode preferred.",
    )

    auth_group = parser.add_argument_group("Authentication")
    auth_group.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin. Prefer native client auth when available.",
    )
    auth_group.add_argument(
        "--prompt-password",
        action="store_true",
        help="Prompt interactively for a password.",
    )
    return parser


def _resolve_runtime_password(
    args: argparse.Namespace, stdin_text: str | None
) -> str | None:
    if args.password_stdin:
        if stdin_text is not None and stdin_text.strip():
            return stdin_text.rstrip("\r\n")
        return sys.stdin.readline().rstrip("\r\n")
    if args.prompt_password:
        return getpass.getpass("Password: ")
    return None


def _execute_query(target: Target, sql: str):
    engine = normalize_engine(target.engine)
    if engine not in {"mysql", "mariadb", "postgres", "sqlite"}:
        raise UsageError(f"Unsupported engine: {target.engine}")

    try:
        if engine in {"mysql", "mariadb"}:
            from .engines.mysql import execute_mysql_query

            return execute_mysql_query(target, sql)
        if engine == "postgres":
            from .engines.postgres import execute_postgres_query

            return execute_postgres_query(target, sql)

        from .engines.sqlite import execute_sqlite_query

        return execute_sqlite_query(target, sql)
    except SqlAgentError:
        raise
    except Exception as exc:
        target_name = target.name or "ephemeral"
        message = _safe_diagnostic_message(exc, target) or type(exc).__name__
        raise QueryExecutionError(
            f"Query failed for target '{target_name}' ({engine}): {message}"
        ) from exc
