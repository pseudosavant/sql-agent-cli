# sql-agent-cli

`sql-agent-cli` is a read-only SQL CLI for agentic workflows.

It is designed to run safe, single-statement queries against configured database targets and return deterministic output that tools like Codex CLI and Claude Code can consume reliably.

V1 targets:

- MySQL
- MariaDB
- PostgreSQL
- SQLite

## Status

Version 0.13.0 adds managed skill synchronization to the pre-1.0 CLI. Its public command, config, JSON, and exit-code contracts are being stabilized for 1.0. Incompatible changes found during testing will be documented.

The current behavior target is defined in [`spec.md`](./spec.md).

## Install and run

Local development:

```text
uv run ./sql_agent_cli.py --help
uv run ./sql_agent_cli.py "SELECT 1"
```

Packaged command target:

```text
uvx sql-agent-cli --help
uvx sql-agent-cli --about
sql-agent-cli "SELECT 1"
```

Install or update the managed `$sql-agent-cli` skill:

```text
uvx sql-agent-cli skill install
```

## Primary usage

Happy path for agents and humans:

```text
sql-agent-cli "SELECT id, name FROM users LIMIT 10"
```

If a default target is configured, that should usually be the first thing you try.
You normally do not need to inspect config files or hunt for environment details
before running a query.

Default target:

```text
sql-agent-cli "SELECT id, name FROM users LIMIT 10"
```

Named target:

```text
sql-agent-cli --target reporting "SELECT COUNT(*) AS total FROM users"
```

Explicit query flag:

```text
sql-agent-cli --target reporting --query "SELECT NOW()"
```

SQL file:

```text
sql-agent-cli --target reporting --sql-file query.sql
```

Stdin:

```text
Get-Content query.sql | sql-agent-cli --target reporting
```

One-off SQLite query, bypassing any configured default target:

```text
sql-agent-cli --engine sqlite --path C:\data\app.db "SELECT * FROM customers LIMIT 5"
```

## Auth

`sql-agent-cli` is designed to prefer native client credential mechanisms over password arguments.

Supported v1 auth patterns:

- PostgreSQL: `PG*` environment variables and `.pgpass`
- MySQL/MariaDB: option files such as `~/.my.cnf`
- Generic fallback: `--password-stdin`
- Optional human fallback: `--prompt-password`

`sql-agent-cli` does not document or guarantee `MYSQL_PWD` as a public credential source.

### Bootstrap native auth files

Seed a PostgreSQL template:

```text
sql-agent-cli config init-native-auth --engine postgres
sql-agent-cli config init-native-auth --engine postgres --target reporting
```

Seed a MySQL template:

```text
sql-agent-cli config init-native-auth --engine mysql
sql-agent-cli config init-native-auth --engine mysql --target dev
```

When `--target NAME` is provided, the tool pre-fills non-secret fields such as host, port, database, and user where possible, while leaving the password blank.

## Config

User config path:

```text
~/.sql-agent-cli/config.toml
```

Example:

```toml
[defaults]
target = "dev"
format = "json"
max_rows = 200
connect_timeout_seconds = 8
query_timeout_seconds = 15

[targets.dev]
engine = "mysql"
host = "az-mysql-pub-sona-asia1-dev.mysql.database.azure.com"
port = 3306
database = "asiadev_2794"
user = "paul"
ssl_mode = "required"

[targets.reporting]
engine = "postgres"
host = "db.example.com"
port = 5432
database = "app"
user = "report_reader"
ssl_mode = "required"

[targets.local_sqlite]
engine = "sqlite"
path = "C:/data/app.db"
```

Config commands:

```text
sql-agent-cli config show
sql-agent-cli config check [--target NAME | --all] [--format text|json]
sql-agent-cli config set-default-target NAME
sql-agent-cli config add-target NAME [options]
sql-agent-cli config remove-target NAME
sql-agent-cli config init-native-auth --engine postgres [--target NAME]
sql-agent-cli config init-native-auth --engine mysql [--target NAME]
sql-agent-cli targets
```

`config show` displays effective target settings and credential-source hints without revealing secrets.

Validate the default target with a safe internal `SELECT 1` after a setup, connection, authentication, or TLS failure:

```text
uvx sql-agent-cli config check --format json
```

Use `--target NAME` to check one named target or `--all` to check every configured target. The command reports non-secret target settings, credential-source availability, and connection status. It returns exit code `1` if any selected target fails. Normal queries should still use the default-target happy path without a mandatory preflight check.

## Project metadata

Show the installed version, summary, project URL, and license:

```text
uvx sql-agent-cli --about
```

## Agent skill

Install or update the user-scoped `$sql-agent-cli` skill:

```text
uvx sql-agent-cli install-skill
```

By default, this writes `~/.agents/skills/sql-agent-cli/SKILL.md`. The skill teaches agentic tools to start with the configured default target, run bounded read-only SQL, parse structured output, and preserve native credential and TLS safety.

Normally installed CLI runs automatically synchronize an already-installed managed skill in this standard location. The running CLI version is the authority. An older, unmodified skill is replaced with the bundled skill. Equal or newer versions are left alone. Nothing is installed automatically, and unmanaged content is never overwritten.

Synchronization is local. It does not query a package index, refresh uv's cache, or update the CLI. Local source checkouts, local source installations, and editable builds skip automatic synchronization. Explicit installation still works from development builds, including `uvx --from . sql-agent-cli skill install`.

The skill records `metadata.managed-by: sql-agent-cli`, a quoted `managed-version`, and `managed-content-sha256` in its YAML front matter. The hash covers the complete UTF-8 text with LF line endings and only the hash value replaced by `""`. It detects modifications, not authenticity. Legacy HTML markers remain recognized. Managed skills with missing or malformed versions receive a fresh replacement, including one-time migration of unversioned legacy skills as version 0.

With valid version metadata, modified skills and skills with missing or invalid hashes are preserved. To replace managed content explicitly:

```text
uvx sql-agent-cli skill install --force
```

Force installation still refuses unmanaged content and never downgrades a newer skill. Normal installation creates a missing skill, updates a pristine older skill, and leaves a current skill unchanged.

Inspect the path, management state, installed and running versions, integrity, and automatic synchronization eligibility without changing files:

```text
uvx sql-agent-cli skill status
uvx sql-agent-cli skill status --format json
```

Use `--skills-dir PATH` with any skill command to select a different skills root. Custom locations require explicit updates and are never discovered by automatic synchronization. Add the same `--skills-dir PATH` when force installing into a custom location.

Remove the managed skill with:

```text
uvx sql-agent-cli skill remove
```

Removal refuses an unmanaged `SKILL.md` unless `--force` is supplied. Only `SKILL.md` is managed. Unrelated files remain in place. The `install-skill` and `remove-skill` aliases and their default JSON results remain supported. All skill commands accept `--format text|json`. Status defaults to text. Installation and removal default to JSON.

Skill commands skip automatic synchronization. Other invocations, including help, version, inspection, and query output, check for updates. Successful updates and preserved older modifications produce concise stderr notices. Maintenance failures never change the primary command's exit status or JSON stdout. Updated instructions affect future skill loading and may not replace instructions already loaded into a running agent session.

## Output

Supported formats:

- `json`
- `markdown`
- `table`
- `csv`

Default format:

- `json`

Stdout is reserved for payload output. Diagnostics and errors go to stderr. Normal query failures emit no stdout payload. `config check --format json` is the intentional exception: it emits its complete diagnostic payload on stdout and returns `1` when any selected target fails.

Successful JSON query output has these stable top-level objects:

```json
{
  "target": {},
  "query": {},
  "result": {
    "columns": [],
    "rows": [],
    "returned_row_count": 0,
    "truncated": false
  }
}
```

Target metadata never includes passwords. Dates and datetimes are ISO 8601 strings, decimals are strings, bytes are base64 strings, and SQL `NULL` is JSON `null`.

`config check --format json` has stable summary fields and one result per selected target:

```json
{
  "checked": 1,
  "succeeded": 1,
  "failed": 0,
  "results": [
    {
      "target": {},
      "credential_hints": {},
      "can_attempt_connection": true,
      "status": "ok"
    }
  ]
}
```

Failed result objects use `status: "error"` and add `error.type` and `error.message` without exposing configured passwords.

Exit codes are part of the public contract:

- `0`: success
- `1`: runtime, connection, driver, timeout, or query-execution failure
- `2`: command usage or SQL validation failure

## Read-only guarantee

V1 is read-only by design.

Intended allowed statement classes include:

- `SELECT`
- `WITH ... SELECT`
- `SHOW`
- `DESCRIBE` / `DESC`
- `EXPLAIN`

The tool rejects mutating or administrative statements before execution and executes exactly one statement per invocation. SQLite `PRAGMA` queries are limited to an explicit read-only allowlist.

The safety model has multiple layers:

- parser-backed validation rejects writes, stacked statements, locking reads, unsafe functions, and mutating SQLite pragmas before connecting
- PostgreSQL and MySQL/MariaDB sessions are configured read-only
- SQLite files are opened in read-only mode
- query timeouts and row limits bound execution and output

These controls are defense in depth, not a substitute for database authorization. Configure targets with dedicated database roles granted only the read and metadata privileges they actually need.

## SSL

Encrypted transport is required by default for network databases.

Supported model:

- `--ssl-mode required`: require TLS and fail if encryption is not negotiated
- `--ssl-mode preferred`: attempt TLS but allow a plaintext fallback
- `--ssl-mode disabled`: prohibit TLS
- `--insecure` as shorthand for `--ssl-mode preferred`

`required` guarantees encryption, not certificate identity verification by itself. Certificate authority and hostname verification depend on the PostgreSQL or MySQL native client trust configuration. Keep `required` unless the user explicitly accepts weaker transport behavior.

## Compatibility policy

The supported config schema consists of the `[defaults]` and `[targets.NAME]` fields shown above. Unknown fields are ignored when reading and may be removed by config-writing commands, so do not use this file as an extension store.

Starting with 1.0.0, this project follows semantic versioning for the documented CLI, config, JSON, stdout/stderr, and exit-code contracts. Additive compatible behavior ships in minor releases; intended breaking changes require a major release. When practical, a deprecated interface will warn for at least one minor release before removal. Security fixes may require faster changes and will be called out explicitly.

## Development direction

Implementation choices currently targeted by the spec:

- `PyMySQL[rsa]` for MySQL and MariaDB
- `psycopg[binary]` for PostgreSQL
- stdlib `sqlite3` for SQLite
- `sqlglot` for parser-backed SQL validation

## Testing

Run the no-network test suite:

```text
uv run --locked python -m unittest discover -v
```

CI runs the no-network suite on Python 3.11, 3.12, and 3.13 on Linux, with an additional Python 3.13 Windows job. It also runs opt-in integration tests against real PostgreSQL and MySQL service containers, including direct checks that the database sessions reject writes even when SQL validation is bypassed.

To run the network tests against local test databases, set `SQL_AGENT_CLI_INTEGRATION=1` and provide `HOST`, `PORT`, `DATABASE`, `USER`, and `PASSWORD` variables under both the `SQL_AGENT_CLI_POSTGRES_*` and `SQL_AGENT_CLI_MYSQL_*` prefixes:

```text
uv run --locked python -m unittest tests.test_network_integration tests.test_read_only_integration -v
```

Run the pinned lint baseline with:

```text
uvx ruff==0.16.1 check .
uvx ruff==0.16.1 format --check .
```

Build and smoke-test an installed wheel in an isolated environment:

```text
uv build --no-sources
uv run --no-project --with ./dist/sql_agent_cli-0.13.0-py3-none-any.whl python tests/wheel_smoke.py
```

The smoke check verifies packaged skill generation, runtime version discovery, and automatic synchronization using a temporary skill directory. Run it with `--expected-source local` or `--expected-source editable` when validating those installation types.

See [`SECURITY.md`](./SECURITY.md) for the security model and vulnerability-reporting guidance, and [`CHANGELOG.md`](./CHANGELOG.md) for release changes.

## License

MIT
