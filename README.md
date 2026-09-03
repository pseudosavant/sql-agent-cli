# sql-agent-cli

`sql-agent-cli` gives people and coding agents a safe, predictable way to inspect MySQL, MariaDB, PostgreSQL, and SQLite databases. It runs one read-only SQL statement at a time, rejects writes before connecting, and returns deterministic output that agents can parse reliably.

## Prerequisite

`sql-agent-cli` is designed to be used with [`uv`](https://docs.astral.sh/uv/getting-started/installation/). Install `uv` before continuing. The documented workflows and managed agent skill use `uvx` to run the tool without requiring a global installation.

## Quick start with an agent

Install the managed agent skill:

```powershell
uvx sql-agent-cli skill install
```

Then use `$sql-agent-cli` in Codex, Claude Code, or another agent harness that supports skills:

> Use $sql-agent-cli to show the ten most recently created users from my configured default database. Include their ID, name, and creation date.

The skill teaches the agent to start with the configured default target, write bounded read-only queries, interpret structured results, and troubleshoot connection problems without exposing credentials.

If you have not configured a database target yet, see [Configure a database target](#configure-a-database-target).

## Manage the agent skill

Inspect, update, or remove the managed skill:

```powershell
uvx sql-agent-cli skill status
uvx sql-agent-cli skill install
uvx sql-agent-cli skill remove
```

Normal runs of an installed CLI keep an existing, unmodified managed skill synchronized with the running CLI version. Missing skills and unmanaged files are left alone. Use `skill install --force` to restore modified managed content.

See [Managed agent skill](docs/agent-skill.md) for integrity metadata, automatic synchronization, custom locations, compatibility aliases, and removal behavior.

## What it returns

JSON is the default output because it is reliable for agents and automation:

```json
{
  "target": {
    "name": "reporting",
    "engine": "postgres",
    "database": "app",
    "user": "reader",
    "host": "db.example.com",
    "port": 5432,
    "ssl_mode": "required"
  },
  "query": {
    "input": "SELECT COUNT(*) AS total FROM users",
    "normalized": "SELECT COUNT(*) AS total FROM users",
    "statement_type": "select"
  },
  "result": {
    "columns": ["total"],
    "rows": [[842]],
    "returned_row_count": 1,
    "truncated": false
  }
}
```

Target metadata never includes passwords. The result reports its columns, rows, returned row count, and whether the configured row limit truncated the output.

See [Output and compatibility contract](docs/output-contract.md) for value serialization, diagnostic output, stable fields, and compatibility guarantees.

## Use the CLI directly

Run a query against the configured default target:

```powershell
uvx sql-agent-cli "SELECT id, name FROM users ORDER BY id LIMIT 10"
```

Select a named target when needed:

```powershell
uvx sql-agent-cli --target reporting "SELECT COUNT(*) AS total FROM users"
```

Query an existing SQLite database without saving a target:

```powershell
uvx sql-agent-cli --engine sqlite --path C:\data\app.db "SELECT * FROM customers LIMIT 5"
```

To install the command as a persistent tool instead:

```powershell
uv tool install sql-agent-cli
```

The examples below continue to use `uvx sql-agent-cli` so they work without a global installation.

## How it works

Every invocation follows the same five steps:

1. Accept exactly one query from an argument, a file, or stdin.
2. Resolve a configured or one-off database target.
3. Parse the SQL and reject unsafe or unsupported statements before connecting.
4. Execute the query in a read-only database session with time and row limits.
5. Write the result in deterministic JSON, Markdown, table, or CSV form.

The default-target happy path is intentionally short:

```powershell
uvx sql-agent-cli "SELECT ..."
```

The CLI resolves the target in this order:

1. A target selected with `--target NAME`
2. A one-off target created with `--engine` and connection options
3. The target named by `[defaults].target` in `~/.sql-agent-cli/config.toml`

## Configure a database target

Saved targets keep connection details out of prompts and scripts. They also make the normal query command easier to remember.

### SQLite

```powershell
uvx sql-agent-cli config add-target local --engine sqlite --path C:\data\app.db
uvx sql-agent-cli config set-default-target local
uvx sql-agent-cli "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
```

### PostgreSQL

```powershell
uvx sql-agent-cli config add-target reporting `
  --engine postgres `
  --host db.example.com `
  --database app `
  --user reader

uvx sql-agent-cli config set-default-target reporting
uvx sql-agent-cli config check
```

### MySQL or MariaDB

```powershell
uvx sql-agent-cli config add-target dev `
  --engine mysql `
  --host db.example.com `
  --database app `
  --user reader

uvx sql-agent-cli config set-default-target dev
uvx sql-agent-cli config check
```

Inspect the effective configuration and available targets without revealing passwords:

```powershell
uvx sql-agent-cli targets
uvx sql-agent-cli config show
uvx sql-agent-cli config check --all
```

The user configuration file is stored at `~/.sql-agent-cli/config.toml`:

```toml
[defaults]
target = "dev"
format = "json"
max_rows = 200
connect_timeout_seconds = 8
query_timeout_seconds = 15

[targets.dev]
engine = "mysql"
host = "db.example.com"
port = 3306
database = "app"
user = "reader"
ssl_mode = "required"

[targets.reporting]
engine = "postgres"
host = "reporting.example.com"
port = 5432
database = "analytics"
user = "report_reader"
ssl_mode = "required"

[targets.local_sqlite]
engine = "sqlite"
path = "C:/data/app.db"
```

## Authentication and TLS

`sql-agent-cli` prefers native database credential mechanisms over password arguments.

| Database | Preferred credential source |
| --- | --- |
| PostgreSQL | `PG*` environment variables or `.pgpass` |
| MySQL and MariaDB | Native option files such as `~/.my.cnf` |
| Any network database | Non-interactive `--password-stdin` fallback |
| Interactive terminal | `--prompt-password` fallback |

The CLI does not document or guarantee `MYSQL_PWD` as a public credential source.

Create a native authentication template and prefill non-secret target values where possible:

```powershell
uvx sql-agent-cli config init-native-auth --engine postgres --target reporting
uvx sql-agent-cli config init-native-auth --engine mysql --target dev
```

Passwords remain blank in generated templates.

Encrypted transport is required by default for network databases:

| Option | Behavior |
| --- | --- |
| `--ssl-mode required` | Require TLS and fail if encryption is not negotiated |
| `--ssl-mode preferred` | Attempt TLS but allow a plaintext fallback |
| `--ssl-mode disabled` | Prohibit TLS |
| `--insecure` | Shorthand for `--ssl-mode preferred` |

`required` guarantees encryption. Certificate authority and hostname verification depend on the database client trust configuration. Keep `required` unless weaker transport behavior is explicitly acceptable.

## Query inputs and output formats

Positional SQL is the normal input form. The explicit alternatives are useful for scripts and longer queries:

```powershell
uvx sql-agent-cli --target reporting --query "SELECT NOW()"
uvx sql-agent-cli --target reporting --sql-file query.sql
Get-Content query.sql | uvx sql-agent-cli --target reporting
```

Provide exactly one query source per invocation.

| Format | Best for |
| --- | --- |
| `json` | Agents, scripts, and automation |
| `markdown` | Reports and chat responses |
| `table` | Terminal inspection |
| `csv` | Import into other tools |

Select a format with `--format`:

```powershell
uvx sql-agent-cli --format table "SELECT id, name FROM users LIMIT 10"
uvx sql-agent-cli --format markdown "SELECT id, name FROM users LIMIT 10"
uvx sql-agent-cli --format csv "SELECT id, name FROM users LIMIT 10"
```

Stdout is reserved for payload output. Diagnostics and errors go to stderr.

## Read-only safety

Supported statement classes include:

- `SELECT`
- `WITH ... SELECT`
- `SHOW`
- `DESCRIBE` and `DESC`
- `EXPLAIN`

The CLI rejects mutating and administrative statements before connecting. It executes exactly one statement per invocation. SQLite `PRAGMA` queries are limited to an explicit read-only allowlist.

The safety model has several layers:

- Parser-backed validation rejects writes, stacked statements, locking reads, unsafe functions, and mutating SQLite pragmas
- PostgreSQL and MySQL or MariaDB sessions are configured read-only
- SQLite files are opened in read-only mode
- Query timeouts and row limits bound execution and output

These controls are defense in depth. They are not a substitute for database authorization. Configure targets with dedicated roles that have only the read and metadata privileges they need.

## Troubleshooting

After a setup, connection, authentication, or TLS failure, safely test the default target with an internal `SELECT 1`:

```powershell
uvx sql-agent-cli config check
uvx sql-agent-cli config check --format json
```

Use `--target NAME` to check one named target or `--all` to check every configured target. The result includes non-secret target settings, credential-source availability, and connection status.

If target selection is unclear, list the configured targets:

```powershell
uvx sql-agent-cli targets --format json
```

Normal queries should start with the default-target happy path. A preflight check is only needed after setup or when troubleshooting a failure.

## Reference

| Command | Purpose |
| --- | --- |
| `uvx sql-agent-cli "SELECT ..."` | Query the default target |
| `uvx sql-agent-cli --target NAME "SELECT ..."` | Query a named target |
| `uvx sql-agent-cli targets` | List targets and identify the default |
| `uvx sql-agent-cli config show` | Show effective non-secret configuration |
| `uvx sql-agent-cli config check` | Test connectivity with a safe `SELECT 1` |
| `uvx sql-agent-cli config --help` | Show target-management commands |
| `uvx sql-agent-cli skill --help` | Show skill-management commands |
| `uvx sql-agent-cli --about` | Show version, project, and license metadata |
| `uvx sql-agent-cli --help` | Show all query and connection options |

Exit codes:

| Code | Meaning |
| ---: | --- |
| `0` | Success |
| `1` | Runtime, connection, driver, timeout, or query-execution failure |
| `2` | Command usage or SQL validation failure |

Normal query failures emit no stdout payload. `config check --format json` is the intentional exception. It emits its complete diagnostic payload on stdout and returns `1` when any selected target fails.

See the [output contract](docs/output-contract.md) for stable JSON fields, value serialization, stdout and stderr behavior, and the compatibility policy.

## Examples

List visible tables on a PostgreSQL or MySQL target through the information schema:

```powershell
uvx sql-agent-cli --target reporting "SELECT table_schema, table_name FROM information_schema.tables ORDER BY table_schema, table_name LIMIT 100"
```

Summarize records without retrieving unnecessary detail:

```powershell
uvx sql-agent-cli --target reporting "SELECT status, COUNT(*) AS total FROM orders GROUP BY status ORDER BY status"
```

Inspect a query plan without running a write:

```powershell
uvx sql-agent-cli "EXPLAIN SELECT id, name FROM users WHERE email = 'user@example.com'"
```

## Status

Version 0.13.0 is a pre-1.0 release. Its command, config, JSON, and exit-code contracts are being stabilized for 1.0. Incompatible changes found during testing will be documented.

The current behavior target is defined in [`spec.md`](./spec.md). See [`CHANGELOG.md`](./CHANGELOG.md) for release history and [`SECURITY.md`](./SECURITY.md) for the security model and vulnerability-reporting guidance.

## Development

Run the CLI from a source checkout:

```powershell
uv run ./sql_agent_cli.py --help
uv run ./sql_agent_cli.py "SELECT 1"
```

Run the no-network test suite and lint checks:

```powershell
uv run --locked python -m unittest discover -v
uvx ruff==0.16.1 check .
uvx ruff==0.16.1 format --check .
```

Build and smoke-test an installed wheel in an isolated environment:

```powershell
uv build --no-sources
uv run --no-project --with ./dist/sql_agent_cli-0.13.0-py3-none-any.whl python tests/wheel_smoke.py
```

CI runs the no-network suite on Python 3.11, 3.12, and 3.13 on Linux, plus Python 3.13 on Windows. It also runs opt-in integration tests against PostgreSQL and MySQL service containers.

To run those integration tests locally, set `SQL_AGENT_CLI_INTEGRATION=1` and provide the documented `SQL_AGENT_CLI_POSTGRES_*` and `SQL_AGENT_CLI_MYSQL_*` environment variables:

```powershell
uv run --locked python -m unittest tests.test_network_integration tests.test_read_only_integration -v
```

## License

MIT
