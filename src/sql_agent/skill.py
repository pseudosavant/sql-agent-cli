from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .validation import UsageError


SKILL_NAME = "sql-agent-cli"
MANAGED_MARKER = "<!-- managed-by: sql-agent-cli -->"


SKILL_MD = f"""---
name: sql-agent-cli
description: Run safe, read-only SQL queries against configured MySQL, MariaDB, PostgreSQL, or SQLite targets using `uvx sql-agent-cli`. Use when the user invokes `$sql-agent-cli`, asks to inspect database data or schema, answer a question from SQL data, compare records, count or summarize rows, or run a specific read-only query.
---

{MANAGED_MARKER}

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
- Start with schema or metadata inspection when table or column names are uncertain; do not guess repeatedly.
- Do not add connection overrides unless the user supplied them or the configured target is insufficient.

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

- Read stderr and correct the reported problem before retrying; do not repeat the same failing command.
- For an unknown or ambiguous target, run `uvx sql-agent-cli targets --format json` and use an exact configured target name.
- For setup or authentication diagnostics, run `uvx sql-agent-cli config show`.
- Do not inspect credential files, change configuration, weaken TLS, or repeatedly guess schema names without user direction.

## Authentication And Configuration

Prefer configured targets and native database credential mechanisms. PostgreSQL can use libpq environment variables or `.pgpass`; MySQL and MariaDB can use client option files. Never print, persist, or place passwords in command arguments.

For setup diagnostics, use the public inspection commands rather than reading secret-bearing files:

```text
uvx sql-agent-cli config show
uvx sql-agent-cli targets
```

Use `--password-stdin` only when the caller provides a password through stdin. Reserve `--prompt-password` for an interactive human session.

For a one-off SQLite database, an explicit path is appropriate:

```text
uvx sql-agent-cli --engine sqlite --path ./app.db "SELECT ..."
```

For network databases, keep secure TLS defaults. Do not use `--insecure` or disable TLS unless the user explicitly accepts that change.
"""


def default_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def skill_dir(skills_dir: Path | None = None) -> Path:
    return (skills_dir or default_skills_dir()) / SKILL_NAME


def install_skill(skills_dir: Path | None = None) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    target.mkdir(parents=True, exist_ok=True)
    skill_path = target / "SKILL.md"
    previous = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    updated = previous != SKILL_MD
    skill_path.write_text(SKILL_MD, encoding="utf-8")
    return {
        "installed": True,
        "updated": updated,
        "skill": SKILL_NAME,
        "path": str(skill_path),
    }


def remove_skill(skills_dir: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    target = skill_dir(skills_dir)
    skill_path = target / "SKILL.md"
    if not target.exists():
        return {"removed": False, "skill": SKILL_NAME, "path": str(target), "reason": "not_installed"}
    if not skill_path.exists():
        raise UsageError(f"refusing to remove '{target}' because SKILL.md is missing.")
    content = skill_path.read_text(encoding="utf-8")
    if MANAGED_MARKER not in content and not force:
        raise UsageError(
            f"refusing to remove '{target}' because it is not marked as managed by sql-agent-cli; "
            "use --force to override."
        )
    shutil.rmtree(target)
    return {"removed": True, "skill": SKILL_NAME, "path": str(target)}
