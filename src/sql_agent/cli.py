from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .arguments import Parser, add_auth_options, add_paths
from .auth import credentials_path, resolve_credential
from .config import (
    build_show_payload,
    can_attempt_connection,
    config_path,
    create_native_auth_template,
    credential_hints_for_target,
    default_port_for_engine,
    load_config,
    normalize_engine,
    resolve_target,
    save_config,
    serialize_show_payload,
)
from .errors import credential_recovery, diagnostic
from .models import AppConfig, Target
from .render import (
    render_config_check_text,
    render_config_show_text,
    render_output,
    render_targets_text,
)
from .skill import install_skill, remove_skill, skill_status, synchronize_skill
from .terminal import masked_input
from .validation import (
    QueryExecutionError,
    SqlAgentError,
    UsageError,
    get_query_text,
    validate_query,
)
from .verification import verify_target

FORMAT_CHOICES = ("json", "markdown", "table", "csv")
ENGINE_CHOICES = ("mysql", "mariadb", "postgres", "postgresql", "sqlite")
SSL_MODE_CHOICES = ("required", "preferred", "disabled", "verify-full")
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
        if args_list and args_list[0] in {"skill", "install-skill", "remove-skill"}:
            return _handle_skill(args_list)
        if args_list and args_list[0] == "setup":
            from .onboarding import setup

            return setup(args_list[1:])
        if args_list and args_list[0] == "config":
            return _handle_config(args_list[1:])
        if args_list and args_list[0] == "targets":
            return _handle_targets(args_list[1:])
        synchronize_skill()
        return _handle_query(args_list)
    except SqlAgentError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (KeyboardInterrupt, EOFError):
        print(
            "Cancelled. Run uvx sql-agent-cli setup in your terminal when ready.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - Driver text must never escape the diagnostic boundary.
        error = diagnostic(exc)
        print(error["message"], file=sys.stderr)
        print("Next: " + error["next_steps"][0], file=sys.stderr)
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

    config = load_config(getattr(args, "config", None))
    target = resolve_target(config, args)
    if not target.engine:
        raise UsageError(
            "No engine resolved. Run uvx sql-agent-cli setup in your terminal, or provide --engine."
        )

    query_text = get_query_text(args, None if args.password_stdin else stdin_text)
    validated = validate_query(query_text, target.engine)
    target.password = _resolve_runtime_password(args, stdin_text)
    credential = resolve_credential(
        target, credentials_path(args.credentials_file, config.source_path)
    )
    target.password = credential.password
    result = _execute_query(
        target, validated.normalized_text, credential_source=credential.source
    )

    output_format = args.format or config.defaults.format or "json"
    print(render_output(output_format, target, validated, result), end="")
    return 0


def _handle_config(argv: list[str]) -> int:
    parser = Parser(
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
    parser.set_defaults(format="text", config=None, credentials_file=None)
    subparsers = parser.add_subparsers(dest="command")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")

    check_parser = subparsers.add_parser(
        "check",
        help="Verify connectivity, read-only session, and catalog access.",
        description=(
            "Verify configured targets using a constant query and bounded catalog query.\n\n"
            "The default target is checked unless --target or --all is supplied.\n"
            "Output includes non-secret target, credential-source, and connection status."
        ),
        formatter_class=HelpFormatter,
    )
    check_parser.add_argument(
        "--offline",
        action="store_true",
        help="Check local readiness without connecting or changing files.",
    )
    check_password = check_parser.add_mutually_exclusive_group()
    check_password.add_argument("--password-stdin", action="store_true")
    check_password.add_argument("--prompt-password", action="store_true")
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
    for subparser in (
        show_parser,
        check_parser,
        set_default,
        add_target,
        remove_target,
        init_auth,
    ):
        add_paths(subparser)

    args = parser.parse_args(argv)
    command = args.command or "show"
    config = load_config(getattr(args, "config", None))

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
    parser = Parser(
        prog=f"{PROGRAM_NAME} targets",
        description=(
            "List configured targets and show which one is the default.\n\n"
            "This is a quick inspection command. Query execution still happens through:\n"
            f'  {PROGRAM_NAME} "SELECT ..."'
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument("--format", choices=ADMIN_FORMAT_CHOICES, default="text")
    add_paths(parser)
    args = parser.parse_args(argv)
    config = load_config(getattr(args, "config", None))
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


def _handle_skill(argv: list[str]) -> int:
    parser = Parser(
        prog=f"{PROGRAM_NAME} skill",
        description=(
            "Manage the sql-agent-cli skill in ~/.agents/skills and in the skills "
            "folder of an existing Claude Code profile. Installation creates the "
            "skills folder if needed. Normal installed CLI runs update "
            "existing, unmodified older copies in those locations. "
            "Local and editable builds skip automatic synchronization."
        ),
        epilog=(
            "Custom locations require explicit updates. The running CLI version is "
            "the authority. This does not update the CLI or uv. Skill commands never "
            "run automatic synchronization."
        ),
    )
    commands = parser.add_subparsers(dest="command")
    install = commands.add_parser(
        "install",
        help="Install or update a managed skill.",
        description="Install a missing skill or update a pristine older managed skill. Modified skills are preserved.",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Replace altered or unverifiable managed content. Never overwrite unmanaged or newer skills.",
    )
    remove = commands.add_parser(
        "remove",
        help="Remove a managed skill.",
        description="Remove the managed sql-agent-cli skill.",
    )
    remove.add_argument(
        "--force", action="store_true", help="Remove even if the skill is not managed."
    )
    status = commands.add_parser(
        "status",
        help="Inspect management, version, integrity, and synchronization eligibility.",
        description="Read skill status without modifying any files.",
    )
    for command in (install, remove, status):
        command.add_argument(
            "--skills-dir",
            type=Path,
            help=(
                "Manage only this skills directory. The defaults are ~/.agents/skills "
                "and the skills folder of an existing Claude Code profile "
                "(CLAUDE_CONFIG_DIR or ~/.claude)."
            ),
        )
        command.add_argument(
            "--format",
            choices=ADMIN_FORMAT_CHOICES,
            default="text" if command is status else "json",
        )
    if argv[0] != "skill":
        argv = [argv[0].removesuffix("-skill"), *argv[1:]]
    else:
        argv = argv[1:]
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "install":
        payload = install_skill(args.skills_dir, force=args.force)
    elif args.command == "remove":
        payload = remove_skill(args.skills_dir, force=args.force)
    else:
        payload = skill_status(args.skills_dir)
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        for index, location in enumerate(payload.get("locations", [payload])):
            if index:
                print()
            for key, value in location.items():
                print(
                    f"{key.replace('_', ' ')}: {json.dumps(value) if not isinstance(value, str) else value}"
                )
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
        ssl_ca=args.ssl_ca,
        auth=args.auth,
        auth_file=args.auth_file,
        service=args.service,
        unix_socket=args.unix_socket,
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
    if args.all and (args.password_stdin or args.prompt_password):
        raise UsageError(
            "Password input requires one target. Use --target NAME instead of --all."
        )
    if args.offline and (args.password_stdin or args.prompt_password):
        raise UsageError(
            "Offline checks do not consume password input. Omit the password flag."
        )
    name = args.target or os.getenv("SQL_AGENT_CLI_TARGET") or config.defaults.target
    names = sorted(config.targets) if args.all else [name] if name else []
    results = []
    code = 0
    path = credentials_path(args.credentials_file, config.source_path)
    for selected in names:
        item = {"target": {"name": selected}, "status": "error"}
        try:
            target = resolve_target(config, argparse.Namespace(target=selected))
            item.update(
                target=target.public_dict(),
                credential_hints=credential_hints_for_target(target),
                can_attempt_connection=can_attempt_connection(target),
            )
            password = _resolve_runtime_password(args, None)
            if password is not None:
                target.password = password
            credential = resolve_credential(target, path)
            target.password = credential.password
            item.update(
                credential_source=credential.source,
                credential_persistence=credential.persistence,
            )
            if args.offline:
                if (
                    not target.engine
                    or target.engine == "sqlite"
                    and (
                        not target.path or not Path(target.path).expanduser().is_file()
                    )
                ):
                    raise UsageError(
                        "The target is incomplete or its SQLite file is missing. Run uvx sql-agent-cli setup."
                    )
                if target.ssl_ca and not Path(target.ssl_ca).expanduser().is_file():
                    raise UsageError(
                        "The selected CA file is missing in this execution environment. Rerun setup --ssl-ca PATH."
                    )
                if target.engine != "sqlite" and not (
                    target.host or target.unix_socket or target.service
                ):
                    raise UsageError(
                        "No server or native connection service is configured. Run uvx sql-agent-cli setup."
                    )
                item["verification"] = {"status": "not_checked"}
                item["status"] = "ok"
            else:
                item["verification"] = verify_target(target)
                if item["verification"]["status"] == "passed":
                    item["status"] = "ok"
                else:
                    item["error"] = credential_recovery(
                        item["verification"]["error"], credential.source, target.name
                    )
                    item["verification"]["error"] = item["error"]
                    code = 1
        except Exception as exc:  # noqa: BLE001 - Driver text must never escape the diagnostic boundary.
            item["error"] = diagnostic(exc, selected)
            code = max(code, getattr(exc, "exit_code", 1))
        results.append(item)
    issues = []
    if not names:
        issues.append(
            "No target selected. Run uvx sql-agent-cli setup in your terminal."
        )
        code = 2
    try:
        skill = skill_status()
    except Exception:  # noqa: BLE001 - Preserve the primary result if optional maintenance fails.
        skill = {"installed": False, "status": "unavailable"}
    unready_skills = [
        location
        for location in skill.get("locations", [skill])
        if not (
            location.get("installed")
            and location.get("managed")
            and location.get("integrity") == "valid"
        )
    ]
    failed = sum(item["status"] == "error" for item in results)
    payload = {
        "checked": len(results),
        "succeeded": len(results) - failed,
        "failed": failed,
        "results": results,
        "offline": args.offline,
        "config_path": str(config.source_path or config_path()),
        "credentials_path": str(path),
        "skill": skill,
        "next_steps": issues,
        "database_ready": None if args.offline else bool(names) and not failed,
        "skill_ready": not unready_skills,
    }
    if unready_skills:
        needs_review = any(
            location.get("installed") or location.get("status") == "unavailable"
            for location in unready_skills
        )
        payload["next_steps"].append(
            "uvx sql-agent-cli skill status"
            if needs_review
            else "uvx sql-agent-cli skill install"
        )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_config_check_text(payload), end="")
    return code


def _add_target_arguments(parser: argparse.ArgumentParser) -> None:
    add_auth_options(parser)
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
    parser = Parser(
        prog=PROGRAM_NAME,
        usage=(
            f'{PROGRAM_NAME} "SELECT ..."\n'
            f'  {PROGRAM_NAME} --target NAME "SELECT ..."\n'
            f'  {PROGRAM_NAME} [connection options] "SELECT ..."\n'
            f"  {PROGRAM_NAME} config --help"
        ),
        description=(
            "Run one read-only SQL query and print deterministic output.\n\n"
            "First run: uvx sql-agent-cli setup\n\n"
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
            "  3. SQL_AGENT_CLI_TARGET, then [defaults].target from ~/.sql-agent-cli/config.toml\n\n"
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
            f"  {PROGRAM_NAME} setup [URL or SQLite path]\n"
            f"  {PROGRAM_NAME} targets\n"
            f"  {PROGRAM_NAME} config show\n"
            f"  {PROGRAM_NAME} config check\n"
            f"  {PROGRAM_NAME} config set-default-target NAME\n"
            f"  {PROGRAM_NAME} skill install [--force]\n"
            f"  {PROGRAM_NAME} skill status [--format json]\n"
            f"  {PROGRAM_NAME} skill remove [--force]\n"
            "  install-skill and remove-skill remain supported aliases.\n\n"
            "Normal runs update existing, unmodified older skills\n"
            "in ~/.agents/skills and in the active Claude Code profile's skills folder.\n"
            "Local and editable builds skip synchronization. See skill --help."
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

    add_paths(parser)
    add_auth_options(parser)
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
        return masked_input()
    return None


def _execute_query(target: Target, sql: str, *, credential_source: str | None = None):
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
        error = credential_recovery(
            diagnostic(exc, target.name), credential_source, target.name
        )
        message = error["message"] + " Next: " + error["next_steps"][0]
        raise QueryExecutionError(
            f"Query failed for target '{target_name}' ({engine}): {message}"
        ) from exc
