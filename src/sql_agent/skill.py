from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml
from packaging.version import InvalidVersion, Version

from . import __version__
from .validation import UsageError

MANAGED_BY = "sql-agent-cli"
SKILL_NAME = "sql-agent-cli"
MANAGED_MARKER = "<!-- managed-by: sql-agent-cli -->"
FORCE_INSTALL_COMMAND = "uvx sql-agent-cli skill install --force"
HASH_FIELD = "managed-content-sha256"
HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
FRONT_MATTER = re.compile(
    r"\A\ufeff?---[ \t]*\n(.*?)^---[ \t]*(?:\n|\Z)", re.MULTILINE | re.DOTALL
)


_SKILL_TEMPLATE = """---
name: sql-agent-cli
description: Run safe, read-only SQL queries against configured MySQL, MariaDB, PostgreSQL, or SQLite targets using `uvx sql-agent-cli`. Use when the user invokes `$sql-agent-cli`, asks to inspect database data or schema, answer a question from SQL data, compare records, count or summarize rows, or run a specific read-only query.
metadata:
  managed-by: sql-agent-cli
  managed-version: {managed_version}
  managed-content-sha256: {content_hash}
---

# SQL Agent CLI

Use the published CLI through `uvx sql-agent-cli` for safe, single-statement, read-only SQL queries. Do not assume `sql-agent-cli` is installed locally or available directly on `PATH`. The CLI validates SQL before execution and returns deterministic output for agentic workflows.

## Start With The Happy Path

When the user asks a database question and does not name a target, try the configured default target first:

```text
uvx sql-agent-cli "SELECT ..."
```

Do not inspect config files, environment variables, or credential files before the first query unless the user asks about setup or the query fails. If the user names a configured environment or database target, select it explicitly:

```text
uvx sql-agent-cli --target reporting "SELECT ..."
```

Use `targets` only when target selection is unclear:

```text
uvx sql-agent-cli targets --format json
```

## Query Safely

- Run exactly one read-only statement per invocation.
- Use `SELECT`, `WITH ... SELECT`, `SHOW`, `DESCRIBE`, or `EXPLAIN` as appropriate.
- Never attempt inserts, updates, deletes, DDL, administrative statements, or write-enabling workarounds.
- Prefer explicit columns, filters, deterministic ordering, aggregates, and a sensible `LIMIT` when exploring data.
- Retrieve only the columns and rows needed for the request. Do not retrieve password hashes, tokens, secrets, or sensitive personal data unless the user explicitly requests and is authorized to access them.
- Treat `max_rows` and output truncation as response guardrails, not database-work limits. Use SQL filters and `LIMIT` to bound the work itself.
- Start with schema or metadata inspection when table or column names are uncertain. Do not guess repeatedly.
- Do not add connection overrides unless the user supplied them or the configured target is insufficient.
- Treat SQL validation and database-session read-only settings as defense in depth. Use targets authenticated as dedicated database roles with only the read and metadata privileges needed for the task.

The CLI rejects mutating SQL, multiple statements, dangerous functions, and `SELECT ... INTO` forms. Treat those guardrails as a hard boundary.

## Query Inputs

Pass only one query source. Positional SQL is the normal form:

```text
uvx sql-agent-cli --target reporting "SELECT id, name FROM users ORDER BY id LIMIT 20"
```

Other supported forms are:

```text
uvx sql-agent-cli --target reporting --query "SELECT COUNT(*) AS total FROM users"
uvx sql-agent-cli --target reporting --sql-file query.sql
```

Use stdin only when it is the clearest way to supply query text. Do not combine positional SQL, `--query`, `--sql-file`, and stdin.

## Output Handling

JSON is the default output. Parse it and answer the user's question with the relevant result, target, row-count, and truncation context instead of dumping the full payload unless raw output is requested. When `truncated` is `true`, say the output is incomplete and refine the query before drawing conclusions that require the full result set.

Use another format only when it helps the requested deliverable:

```text
uvx sql-agent-cli --format markdown "SELECT ..."
uvx sql-agent-cli --format table "SELECT ..."
uvx sql-agent-cli --format csv "SELECT ..."
```

Stdout is payload only. Treat stderr as diagnostics and errors.

## Recover From Failures

- Read stderr and correct the reported problem before retrying. Do not repeat the same failing command.
- For an unknown or ambiguous target, run `uvx sql-agent-cli targets --format json` and use an exact configured target name.
- After a setup, connection, authentication, or TLS failure, run `uvx sql-agent-cli config check --format json` once for the default target, or add `--target NAME` for a named target. Do not run this preflight before a normal first query.
- If configuration or credentials are missing, tell the user to run `uvx sql-agent-cli setup` in their terminal. Do not run interactive setup in an agent harness.
- For remote hosts, containers, or sandboxes, run `uvx sql-agent-cli config check --offline --format json` in that execution environment. A terminal login or local file does not establish agent access.
- Do not inspect credential files, change configuration, weaken TLS, or repeatedly guess schema names without user direction.

## Authentication And Configuration

Prefer configured targets. The CLI resolves saved credentials, explicit environment overrides, and native authentication. PostgreSQL supports libpq sources. MySQL and MariaDB support client option files. Never read credential files, request credentials in agent chat, print them, or place them in command arguments.

For setup diagnostics, use the public inspection commands rather than reading secret-bearing files:

```text
uvx sql-agent-cli config check --format json
uvx sql-agent-cli config show
uvx sql-agent-cli targets
```

Use `--password-stdin` only when the caller provides a password through stdin. Reserve `--prompt-password` for an interactive human session.

For a one-off SQLite database, an explicit path is appropriate:

```text
uvx sql-agent-cli --engine sqlite --path ./app.db "SELECT ..."
```

Setup installs this skill in `~/.agents/skills/sql-agent-cli/SKILL.md`. The project does not manage agent-specific copies.

For network databases, keep secure TLS defaults. New setup targets use `--ssl-mode verify-full`, which verifies the certificate and hostname. Existing `--ssl-mode required` settings guarantee encryption without guaranteeing server identity. Do not use `--insecure` or disable TLS unless the user explicitly accepts that change.
"""


def default_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def skill_dir(skills_dir: Path | None = None) -> Path:
    return (skills_dir or default_skills_dir()) / SKILL_NAME


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def render_skill(version: str | None = None) -> str:
    """Render the sole skill template, including its own normalized content hash."""
    text = _normalize(
        _SKILL_TEMPLATE.format(
            managed_version=json.dumps(__version__ if version is None else version),
            content_hash='""',
        )
    )
    return text.replace(f'{HASH_FIELD}: ""', f'{HASH_FIELD}: "{_digest(text)}"', 1)


def _version(value: str | None) -> Version | None:
    try:
        return Version(value) if value is not None else None
    except InvalidVersion:
        return None


def _mapping(node: yaml.Node | None) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        raise UsageError("Skill front matter and metadata must be YAML mappings.")
    result = {}
    for key, value in node.value:
        name = _string(key)
        if name is None or name in result:
            raise ValueError("Ambiguous skill metadata keys.")
        result[name] = value
    return result


def _string(node: yaml.Node | None) -> str | None:
    if isinstance(node, yaml.ScalarNode) and node.tag == "tag:yaml.org,2002:str":
        return node.value
    return None


@dataclass(frozen=True)
class SkillState:
    content: str | None = None
    managed: bool = False
    version: str | None = None
    integrity: str = "not_applicable"
    recovery: bool = False

    @property
    def installed(self) -> bool:
        return self.content is not None


def inspect_skill(content: str) -> SkillState:
    """Parse management fields without reserializing the text used for hashing."""
    normalized = _normalize(content)
    front = FRONT_MATTER.match(normalized)
    fields = {}
    if front:
        root = _mapping(yaml.compose(front[1], Loader=yaml.SafeLoader))
        if "metadata" in root:
            fields = _mapping(root["metadata"])
    elif normalized.lstrip("\ufeff").startswith("---"):
        raise UsageError("Skill front matter is incomplete.")
    legacy = MANAGED_MARKER in content
    managed = (
        _string(fields["managed-by"]) == MANAGED_BY
        if "managed-by" in fields
        else legacy
    )
    if not managed:
        return SkillState(content=content)

    version = _string(fields.get("managed-version"))
    if _version(version) is None:
        is_legacy = legacy and "managed-version" not in fields
        return SkillState(
            content,
            True,
            "0" if is_legacy else None,
            "legacy" if is_legacy else "not_applicable",
            True,
        )

    hash_node = fields.get(HASH_FIELD)
    stored_hash = _string(hash_node)
    if hash_node is None:
        integrity = "missing"
    elif stored_hash is None or not HASH_PATTERN.fullmatch(stored_hash):
        integrity = "malformed"
    else:
        # YAML source marks select only this value, including its quotes.
        # Aliases point to a different value. They cannot be hashed this way.
        start = hash_node.start_mark.index
        end = hash_node.end_mark.index
        metadata_node = root["metadata"]
        hash_key = next(
            key for key, _ in metadata_node.value if _string(key) == HASH_FIELD
        )
        if not hash_key.end_mark.index <= start < end <= metadata_node.end_mark.index:
            integrity = "malformed"
        else:
            offset = front.start(1)
            empty = normalized[: offset + start] + '""' + normalized[offset + end :]
            integrity = "valid" if _digest(empty) == stored_hash else "altered"
    return SkillState(content, True, version, integrity)


def _check_path(path: Path) -> None:
    for item in (path.parent, path):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise UsageError(f"refusing to manage linked skill path '{item}'.")
    if path.parent.exists() and not path.parent.is_dir():
        raise UsageError(f"expected a skill directory at '{path.parent}'.")
    if path.exists() and not path.is_file():
        raise UsageError(f"expected a skill file at '{path}'.")


def _read_skill(path: Path) -> SkillState:
    _check_path(path)
    try:
        content = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return SkillState()
    return inspect_skill(content)


def runtime_source() -> str:
    """Identify the imported code, independently of its launcher or working directory."""
    module = Path(__file__).resolve()
    checkout = any(
        (parent / "pyproject.toml").is_file() or (parent / ".git").is_dir()
        for parent in list(module.parents)[:3]
    )
    try:
        dist = metadata.distribution(MANAGED_BY)
        direct_url = dist.read_text("direct_url.json")
        if direct_url:
            source = json.loads(direct_url)
            if source.get("dir_info", {}).get("editable"):
                return "editable"
            url = urlsplit(source["url"])
            if url.scheme == "file":
                # A built wheel is a normal installation, even from a local artifact.
                wheel = unquote(url.path).lower().endswith(".whl")
                if "dir_info" in source or not wheel:
                    return "local"
        if checkout:
            return "local"
        if dist.files and any(
            Path(dist.locate_file(entry)).resolve() == module for entry in dist.files
        ):
            return "installed"
    except (
        metadata.PackageNotFoundError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ):
        pass
    return "local" if checkout else "unknown"


def _update_reason(state: SkillState, current: Version | None) -> str:
    if not state.installed:
        return "not_installed"
    if not state.managed:
        return "unmanaged"
    if current is None:
        return "invalid_cli_version"
    if state.recovery:
        return "recovery"
    installed = _version(state.version)
    if installed == current:
        return "current"
    if installed > current:
        return "newer"
    return "older" if state.integrity == "valid" else "altered"


def skill_status(skills_dir: Path | None = None) -> dict[str, Any]:
    path = skill_dir(skills_dir) / "SKILL.md"
    state = _read_skill(path)
    current = _version(__version__)
    installed = _version(state.version)
    comparison = "not_applicable"
    if current is not None and installed is not None:
        comparison = (
            "older"
            if installed < current
            else "newer"
            if installed > current
            else "equal"
        )
    source = runtime_source()
    standard = path.absolute() == (skill_dir() / "SKILL.md").absolute()
    reason = _update_reason(state, current)
    if source != "installed":
        reason = (
            "local_development"
            if source in {"local", "editable"}
            else "unknown_runtime_source"
        )
    elif not standard:
        reason = "custom_directory"
    return {
        "skill": SKILL_NAME,
        "path": str(path),
        "standard_location": standard,
        "installed": state.installed,
        "managed": state.managed,
        "cli_version": __version__,
        "installed_version": state.version,
        "version_comparison": comparison,
        "integrity": state.integrity,
        "automatic_sync_eligible": reason in {"older", "recovery"},
        "automatic_sync_reason": reason,
        "runtime_source": source,
        "local_development": source in {"local", "editable"},
        "force_install_command": FORCE_INSTALL_COMMAND
        if state.integrity in {"altered", "missing", "malformed"}
        and comparison != "newer"
        else None,
    }


def _atomic_replace(path: Path, text: str, expected: SkillState) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".SKILL.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck after preparing the replacement. Never overwrite an observed
        # edit, removal, or newer version from another invocation.
        if _read_skill(path) != expected:
            raise UsageError(
                f"skill changed during update at '{path}'. Run the command again."
            )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def install_skill(
    skills_dir: Path | None = None, *, force: bool = False
) -> dict[str, Any]:
    path = skill_dir(skills_dir) / "SKILL.md"
    state = _read_skill(path)
    reason = _update_reason(state, _version(__version__))
    if state.installed and not state.managed:
        raise UsageError(f"refusing to overwrite unmanaged skill '{path}'.")
    if state.installed and reason == "invalid_cli_version":
        raise UsageError(
            "Cannot compare the running CLI version with the installed skill."
        )
    if (
        reason != "newer"
        and not state.recovery
        and state.managed
        and state.integrity != "valid"
        and not force
    ):
        raise UsageError(
            f"refusing to overwrite altered or unverifiable skill '{path}'. Use `{FORCE_INSTALL_COMMAND}`."
        )
    canonical = render_skill()
    updated = (
        reason != "newer"
        and (force or reason != "current")
        and (state.content is None or _normalize(state.content) != canonical)
    )
    if updated:
        if not state.installed and path.parent.exists() and any(path.parent.iterdir()):
            raise UsageError(
                f"refusing to install into nonempty directory '{path.parent}' without SKILL.md."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_replace(path, canonical, state)
    return {
        "installed": True,
        "updated": updated,
        "skill": SKILL_NAME,
        "path": str(path),
    }


def synchronize_skill() -> None:
    """Best-effort local maintenance. This must never fail the primary command."""
    try:
        current = _version(__version__)
        if current is None or runtime_source() != "installed":
            return
        path = skill_dir() / "SKILL.md"
        state = _read_skill(path)
        reason = _update_reason(state, current)
        if reason == "altered":
            print(
                f"Skill at '{path}' is altered or unverifiable. Use `{FORCE_INSTALL_COMMAND}` to replace it.",
                file=sys.stderr,
            )
        elif reason in {"older", "recovery"}:
            _atomic_replace(path, render_skill(), state)
            print(
                f"Updated skill {state.version or 'unknown'} -> {__version__} at '{path}'.",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001 - maintenance must not fail a command.
        message = " ".join(str(exc).split()) or type(exc).__name__
        print(f"Skill synchronization skipped: {message}", file=sys.stderr)


def remove_skill(
    skills_dir: Path | None = None, *, force: bool = False
) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    path = target / "SKILL.md"
    _check_path(path)
    if not target.exists():
        return {
            "removed": False,
            "skill": SKILL_NAME,
            "path": str(target),
            "reason": "not_installed",
        }
    if not path.exists():
        raise UsageError(f"refusing to remove '{target}' because SKILL.md is missing.")
    if not force and not _read_skill(path).managed:
        raise UsageError(
            f"refusing to remove '{target}' because it is not marked as managed by sql-agent-cli. "
            "Use --force to override."
        )
    path.unlink()
    if not any(target.iterdir()):
        target.rmdir()
    return {"removed": True, "skill": SKILL_NAME, "path": str(target)}
