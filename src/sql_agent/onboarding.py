from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from . import terminal
from .arguments import Parser, add_paths
from .auth import (
    credentials_path,
    environment_credential,
    remove_credential,
    resolve_credential,
    save_credential,
)
from .config import (
    credential_hints_for_target,
    load_config,
    normalize_engine,
    resolve_target,
    save_config,
    validate_target,
)
from .errors import (
    CredentialError,
    UsageError,
    credential_recovery,
    diagnostic,
    setup_command,
)
from .models import AppConfig, Target
from .skill import install_skill, skill_dir
from .verification import verify_target


def parse_resource(value: str) -> Target:
    """Accept a password-free database URI or a SQLite path, never a DSN."""
    if "://" not in value:
        return Target(engine="sqlite", path=value)
    try:
        uri = urlsplit(value)
        engine = normalize_engine(uri.scheme)
        if uri.password is not None:
            raise ValueError
        if engine not in {"postgres", "mysql", "mariadb", "sqlite"} or uri.fragment:
            raise ValueError
        if engine == "sqlite":
            if uri.netloc or uri.query:
                raise ValueError
            return Target(engine="sqlite", path=unquote(uri.path))
        target = Target(
            engine=engine,
            host=uri.hostname,
            port=uri.port,
            user=unquote(uri.username) if uri.username else None,
            database=unquote(uri.path.lstrip("/")) or None,
        )
        aliases = {
            "sslmode": "ssl_mode",
            "ssl_mode": "ssl_mode",
            "sslrootcert": "ssl_ca",
            "ssl_ca": "ssl_ca",
        }
        seen = set()
        for key, val in parse_qsl(
            uri.query, keep_blank_values=True, strict_parsing=True
        ):
            field = aliases.get(key)
            if not field or field in seen or not val:
                raise ValueError
            seen.add(field)
            if field == "ssl_mode":
                val = {
                    "require": "required",
                    "prefer": "preferred",
                    "disable": "disabled",
                }.get(val, val)
            setattr(target, field, val)
        validate_target(target)
        return target
    except (ValueError, UsageError):
        raise UsageError(
            "Invalid resource URL. Use a postgres://, mysql://, or mariadb:// URL without a password. Only TLS mode and CA query options are accepted. Enter passwords at the masked terminal prompt."
        ) from None


def setup(argv: list[str]) -> int:
    from .cli import _add_target_arguments

    parser = Parser(
        prog="sql-agent-cli setup",
        description=(
            "Verify a database, save its target, and install the managed skill in "
            "~/.agents/skills and the skills folder of an existing Claude Code profile. "
            "Enter passwords only at the masked terminal prompt."
        ),
    )
    parser.add_argument(
        "resource",
        nargs="?",
        help="Password-free database URL or existing SQLite file.",
    )
    parser.add_argument(
        "--target",
        help="Target to create or update. The first target is named default.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt or consume stdin. Use saved, native, or environment credentials.",
    )
    parser.add_argument(
        "--replace-password",
        action="store_true",
        help="Verify and replace a saved password using a masked terminal prompt.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Setup report format. Defaults to text. Use json for automation.",
    )
    add_paths(parser)
    _add_target_arguments(parser)
    args = parser.parse_args(argv)
    report = {
        "status": "failed",
        "database_ready": False,
        "configuration_saved": False,
        "skill_ready": False,
    }
    try:
        _setup(args, report)
        code = 0
    except VerificationFailure as exc:
        report["error"] = exc.error
        code = 1
    except (KeyboardInterrupt, EOFError):
        report["error"] = diagnostic(
            UsageError(
                "Setup cancelled. Rerun uvx sql-agent-cli setup in your terminal."
            )
        )
        code = 2
    except Exception as exc:  # noqa: BLE001 - Driver text must never escape the diagnostic boundary.
        report["error"] = diagnostic(exc, report.get("target", {}).get("name"))
        code = getattr(exc, "exit_code", 1)
    if (
        report.get("error")
        and report["configuration_saved"]
        and not report["skill_ready"]
    ):
        report["error"]["message"] = (
            "Database configuration was saved, but the managed skill is not ready. "
            + report["error"]["message"]
        )
        report["error"]["next_steps"] = ["uvx sql-agent-cli skill status"]
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(_render(report), end="")
    if report.get("error"):
        print("\n" + report["error"]["message"], file=sys.stderr)
        for step in report["error"]["next_steps"]:
            print("\nNext:\n  " + step, file=sys.stderr)
    return code


def _setup(args, report):
    interactive = not args.non_interactive and terminal.is_interactive()
    config = load_config(args.config)
    secrets = credentials_path(args.credentials_file, config.source_path)
    report.update(
        config_path=str(config.source_path),
        credentials_path=str(secrets),
        skill_path=str(skill_dir() / "SKILL.md"),
    )
    if args.replace_password and not interactive:
        raise UsageError(
            "Password replacement needs an interactive terminal without password environment overrides. Remove those overrides from this process, then rerun setup --replace-password."
        )
    resource = parse_resource(args.resource) if args.resource else Target()
    explicit = Target(
        **{
            f.name: getattr(args, f.name, None)
            for f in fields(Target)
            if f.name not in {"name", "password", "credential_id"}
        }
    )
    explicit.engine = normalize_engine(explicit.engine)
    for f in fields(Target):
        left, right = getattr(resource, f.name), getattr(explicit, f.name)
        if left is not None and right is not None and left != right:
            raise UsageError(
                "The resource and connection flags disagree. Supply the value only once, then rerun setup."
            )
    overlay = resource.merged(explicit)
    name = args.target or os.getenv("SQL_AGENT_CLI_TARGET") or config.defaults.target
    if name is None:
        if len(config.targets) == 1:
            name = next(iter(config.targets))
        elif config.targets:
            if not interactive:
                raise UsageError(
                    "Multiple targets exist without a default. Run uvx sql-agent-cli targets, then rerun setup --target NAME."
                )
            print(
                "\nExisting targets: " + ", ".join(sorted(config.targets)),
                file=sys.stderr,
            )
            name = terminal.ask("Target name: ")
        else:
            name = "default"
    base = config.targets.get(name, Target(name=name))
    if base.engine and overlay.engine and base.engine != overlay.engine:
        if not args.target:
            raise UsageError(
                "This resource uses a different engine from the default target. Rerun setup with --target NAME to choose which target to create or update."
            )
        base = Target(name=name)
    target = base.merged(overlay)
    target.name = name
    if not target.engine:
        _required(
            target,
            "engine",
            "Database engine [postgres, mysql, mariadb, sqlite]: ",
            interactive,
        )
        target.engine = normalize_engine(target.engine)
    validate_target(target)
    # Resolve native details before asking questions. Keep new network targets verified.
    if target.engine != "sqlite" and target.ssl_mode is None:
        target.ssl_mode = "verify-full"
    working = AppConfig(config.defaults, {name: target})
    target = resolve_target(working, argparse.Namespace(target=name))
    if target.engine == "sqlite":
        if args.replace_password:
            raise UsageError(
                "SQLite does not use a password. Rerun setup without --replace-password."
            )
        _required(target, "path", "Existing SQLite database path: ", interactive)
        target.path = str(Path(target.path).expanduser().resolve())
    else:
        for field, label in (
            ("host", "Server hostname: "),
            ("database", "Database name: "),
            ("user", "Database login: "),
        ):
            if field == "host" and target.unix_socket:
                continue
            _required(target, field, label, interactive)
    for field in ("auth_file", "ssl_ca", "unix_socket"):
        value = getattr(target, field)
        if value:
            setattr(target, field, str(Path(value).expanduser().resolve()))
    validate_target(target)
    if args.replace_password and environment_credential(target.engine):
        raise UsageError(
            "Password replacement cannot verify a shadowed saved credential. Remove password environment overrides from this process, then rerun setup --replace-password."
        )
    report["target"] = target.public_dict()
    entered = False
    try:
        credential = resolve_credential(target, secrets)
    except CredentialError:
        if not interactive:
            raise
        credential = None
    needs_password = args.replace_password or credential is None
    if (
        credential
        and credential.source == "native"
        and target.auth != "native"
        and not _native_available(target)
    ):
        needs_password = True
    if needs_password:
        if not interactive:
            raise UsageError(
                "No credential source is available. Run uvx sql-agent-cli setup in your terminal, or supply SQL_AGENT_CLI_PASSWORD in this execution environment and rerun setup --non-interactive. Use --auth native for passwordless native authentication."
            )
        _credential_instructions(target, secrets)
        target.password = terminal.masked_input()
        credential = resolve_credential(target, secrets)
        entered = True
    else:
        target.password = credential.password
    report.update(
        credential_source=credential.source,
        credential_persistence="file" if entered else credential.persistence,
    )
    print("\nVerifying the connection and a bounded catalog query...", file=sys.stderr)
    verification = verify_target(target)
    if (
        interactive
        and not entered
        and target.auth != "native"
        and credential.source in {"native", "managed_file"}
        and verification.get("error", {}).get("code") == "authentication_failed"
    ):
        print(
            "\nThe existing credential was rejected. Enter a replacement to verify before saving. Existing files are still unchanged.",
            file=sys.stderr,
        )
        _credential_instructions(target, secrets)
        target.password = terminal.masked_input()
        credential = resolve_credential(target, secrets)
        entered = True
        report.update(
            credential_source=credential.source, credential_persistence="file"
        )
        print("\nVerifying the replacement...", file=sys.stderr)
        verification = verify_target(target)
    report["verification"] = verification
    if verification["status"] != "passed":
        error = credential_recovery(
            verification["error"], credential.source, target.name
        )
        if credential.source in {"SQL_AGENT_CLI_PASSWORD", "PGPASSWORD", "native"}:
            error["next_steps"] = [setup_command(target.name)]
        verification["error"] = error
        report["error"] = error
        # No configuration, credential, or skill mutation has happened.
        raise VerificationFailure(error)
    report["database_ready"] = True
    print(
        "\nConnection, read-only session, and catalog query verified.\nApplication table access and the account's write privileges were not tested.",
        file=sys.stderr,
    )
    if verification.get("notice"):
        print("\n" + verification["notice"], file=sys.stderr)
    old_id = config.targets.get(name, Target()).credential_id
    new_id = None
    try:
        if entered:
            new_id = save_credential(target, target.password, secrets)
            target.credential_id, target.auth = new_id, "managed"
        elif target.auth == "native":
            target.credential_id = None
        target.password = None
        config.targets[name] = target
        if not config.defaults.target:
            config.defaults.target = name
        save_config(config)
    except Exception:
        if new_id:
            remove_credential(new_id, secrets)
        raise
    report.update(
        configuration_saved=True,
        target=target.public_dict(),
        default_target=config.defaults.target,
    )
    if (
        old_id
        and old_id != target.credential_id
        and not any(t.credential_id == old_id for t in config.targets.values())
    ):
        try:
            remove_credential(old_id, secrets)
        except Exception:  # noqa: BLE001 - Preserve the primary result if optional maintenance fails.
            report["credential_cleanup"] = (
                "An unused older credential entry could not be removed. The new configuration is usable."
            )
    report["skill"] = install_skill()
    report.update(
        skill_ready=True,
        status="ready",
        next_steps=[
            "Start a new agent session. Ask it to use the sql-agent-cli skill to list tables and explain their columns."
        ],
    )


class VerificationFailure(Exception):
    def __init__(self, error):
        self.error = error


def _required(target, field, prompt, interactive):
    if getattr(target, field):
        return
    if not interactive:
        raise UsageError(
            f"Missing --{field}. Supply that connection option and rerun uvx sql-agent-cli setup --non-interactive, or run setup in your terminal."
        )
    value = terminal.ask(prompt)
    if not value:
        raise UsageError(
            f"A value for --{field} is required. Rerun uvx sql-agent-cli setup."
        )
    setattr(target, field, value)


def _native_available(target):
    hints = credential_hints_for_target(target)
    candidates = hints.get("pgpass_candidates", hints.get("option_file_candidates", []))
    return bool(
        target.service
        or target.auth_file
        or target.unix_socket
        or os.getenv("PGSERVICE")
        and target.engine == "postgres"
        or any(item["exists"] for item in candidates)
    )


def _credential_instructions(target, path):
    print(
        "\nUse a dedicated database login with read access. Ask the database owner to create it if needed.",
        file=sys.stderr,
    )
    if target.engine == "postgres":
        print(
            "\nRequired: CONNECT on this database, USAGE on the selected schemas, and SELECT on the tables or views you need.\nPasswords expire only when the login or service policy sets an expiry. Renew with the database owner.\n\nhttps://www.postgresql.org/docs/current/sql-grant.html\nhttps://www.postgresql.org/docs/current/sql-createrole.html",
            file=sys.stderr,
        )
    else:
        domain = (
            "https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/create-user"
            if target.engine == "mariadb"
            else "https://dev.mysql.com/doc/refman/8.4/en/create-user.html"
        )
        print(
            "\nRequired: SELECT on the tables or views you need. SHOW VIEW is needed only for view definitions. The login's host rule must allow this machine.\nPassword expiry depends on account and server policy. Ask the database owner about renewal.\n\n"
            + domain,
            file=sys.stderr,
        )
    print(
        f"\nThe password is masked with asterisks. It will be saved only after verification.\nUse environment credentials for short-lived cloud tokens. Setup does not refresh them.\n\nSeparate credentials file:\n  {path}\n\nThis file uses normal inherited permissions. Anyone who can read it can use the saved password.\n",
        file=sys.stderr,
    )


def _render(report):
    lines = ["Setup complete." if report["status"] == "ready" else "Setup incomplete."]
    for field, label in (
        ("config_path", "Configuration"),
        ("credentials_path", "Credentials"),
    ):
        if report.get(field):
            lines.extend(["", label + ":", "  " + report[field]])
    if report.get("skill_path"):
        lines.extend(["", "Managed skill:"])
        locations = report.get("skill", {}).get("locations")
        if locations:
            lines.extend("  " + location["path"] for location in locations)
        else:
            lines.append("  " + report["skill_path"])
    lines.extend(
        [
            "",
            "Database verified: " + str(report["database_ready"]).lower(),
            "Configuration saved: " + str(report["configuration_saved"]).lower(),
            "Skill ready: " + str(report["skill_ready"]).lower(),
        ]
    )
    if report.get("credential_source"):
        lines.extend(
            [
                "",
                "Credential source: " + report["credential_source"],
                "Credential persistence: " + report["credential_persistence"],
            ]
        )
    for line in report.get("next_steps", []):
        lines.extend(["", line])
    return "\n".join(lines) + "\n"
