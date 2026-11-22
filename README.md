# sql-agent-cli

`sql-agent-cli` gives people and coding agents a safe, predictable way to inspect MySQL, MariaDB, PostgreSQL, and SQLite databases. It runs one read-only SQL statement at a time, rejects writes before connecting, and returns deterministic output that agents can parse reliably.

## Quick start with an agent

You need an existing database and access to it from the machine where your agent runs. For a network database, obtain its hostname, database name, and a login with read access. A password-free connection URL can supply those details. SQLite needs only an existing database file.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run setup in your terminal. `uvx` obtains the CLI and its Python dependencies. No database client installation is required.

### PowerShell

```powershell
# Install uv if it is not already available.
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Open a new PowerShell terminal after installation.
uvx sql-agent-cli setup
```

### Bash

```bash
# Install uv if it is not already available.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Open a new terminal after installation.
uvx sql-agent-cli setup
```

Setup asks for missing connection details, masks password input, and verifies the database. It saves the target and any entered password separately. It installs the managed skill in `~/.agents/skills/sql-agent-cli/SKILL.md`. If your Claude Code profile directory exists, setup also installs the skill in its `skills` folder and creates that folder if needed. The default profile directory is `~/.claude`.

To skip connection questions, supply a password-free URL. These commands work in both shells. Replace the example connection details with yours:

```text
uvx sql-agent-cli setup "postgres://reader@db.example.com/app"
uvx sql-agent-cli setup "mysql://reader@db.example.com/app"
uvx sql-agent-cli setup "mariadb://reader@db.example.com/app"
```

For SQLite, use an existing file:

```powershell
uvx sql-agent-cli setup 'C:\data\app.db'
```

```bash
uvx sql-agent-cli setup "$HOME/data/app.db"
```

Start a new agent session and try this request.

> Use the sql-agent-cli skill to list the tables in my default database and explain their columns. Do not read application rows yet.

In local Claude Code, invoke the skill with `/sql-agent-cli`. If you start using Claude Code after setup, run `uvx sql-agent-cli skill install` once your profile exists. A remote agent or container needs its own reachable database, configuration, credentials, and skill files.

See [Setup and authentication](docs/setup.md) for required permissions, native authentication, private CAs, password renewal, multiple targets, and troubleshooting.

## Manage the agent skill

Use these commands to inspect, install, or remove the managed skill. They work before you configure a database.

```powershell
uvx sql-agent-cli skill status
uvx sql-agent-cli skill install
uvx sql-agent-cli skill remove
```

A plain `uvx sql-agent-cli` updates existing, unmodified older copies. Installing a missing copy requires `setup` or `skill install`. Use `skill install --force` to restore modified managed content.

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
3. The target named by `SQL_AGENT_CLI_TARGET`
4. The target named by `[defaults].target` in `~/.sql-agent-cli/config.toml`

## Configure a database target

The first successful setup creates a default target. Repeat setup to verify and reuse it. Add a named target without changing the existing default:

```text
uvx sql-agent-cli setup
uvx sql-agent-cli setup "postgres://reader@db.example.com/analytics" --target reporting
uvx sql-agent-cli config set-default-target reporting
```

Inspect configuration without exposing passwords or changing files:

```text
uvx sql-agent-cli targets
uvx sql-agent-cli config show
uvx sql-agent-cli config check --offline
uvx sql-agent-cli config check --all
```

Ordinary settings live in `~/.sql-agent-cli/config.toml`. Entered passwords live in a separate `credentials.toml` beside it. Files use normal inherited permissions. The [setup guide](docs/setup.md) explains overrides and agent access.

Existing `config add-target`, `config set-default-target`, `config init-native-auth`, `--password-stdin`, and `--prompt-password` commands remain available.

## Authentication and TLS

Setup reuses saved credentials or native authentication when available. Otherwise it offers a masked password prompt. `SQL_AGENT_CLI_PASSWORD` overrides saved or native credentials for the current execution environment. PostgreSQL also accepts `PGPASSWORD`. Neither is saved by setup.

New network targets created by setup default to `verify-full`, which verifies the server certificate and hostname. Supply `--ssl-ca PATH` for a private CA. Existing targets keep their TLS setting.

| Option | Behavior |
| --- | --- |
| `--ssl-mode verify-full` | Require TLS and verify the certificate and hostname |
| `--ssl-mode required` | Require encryption without guaranteeing server identity |
| `--ssl-mode preferred` | Attempt TLS but allow plaintext fallback |
| `--ssl-mode disabled` | Disable TLS |
| `--insecure` | Query shorthand for `--ssl-mode preferred` |

Legacy direct connections still default to `required`. The tool never weakens TLS automatically.

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

After a connection failure, check the default target with a constant query and a bounded catalog query:

```powershell
uvx sql-agent-cli config check
uvx sql-agent-cli config check --format json
```

Use `--target NAME` to check one named target or `--all` to check every configured target. The result includes non-secret target settings, credential-source availability, and connection status.

If target selection is unclear, list the configured targets:

```powershell
uvx sql-agent-cli targets --format json
```

Normal queries should start with the default-target happy path. Setup already verifies the target. A check is useful when troubleshooting a later failure.

## Reference

| Command | Purpose |
| --- | --- |
| `uvx sql-agent-cli "SELECT ..."` | Query the default target |
| `uvx sql-agent-cli --target NAME "SELECT ..."` | Query a named target |
| `uvx sql-agent-cli targets` | List targets and identify the default |
| `uvx sql-agent-cli config show` | Show effective non-secret configuration |
| `uvx sql-agent-cli setup` | Verify and save a database, then install its skill |
| `uvx sql-agent-cli config check` | Check connectivity, read-only session, and catalog access |
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

Normal query failures emit no stdout payload. `setup --format json` and `config check --format json` emit structured diagnostic reports even on failure. They return `1` for runtime failures and `2` for missing or invalid input.

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

Version 0.15.0 is a pre-1.0 release. Its command, config, JSON, and exit-code contracts are being stabilized for 1.0. Incompatible changes found during testing will be documented.

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
uv run --no-project --with ./dist/sql_agent_cli-0.15.0-py3-none-any.whl python tests/wheel_smoke.py
```

CI runs the no-network suite on Python 3.11, 3.12, and 3.13 on Linux, plus Python 3.13 on Windows. It also runs opt-in integration tests against PostgreSQL and MySQL service containers.

To run those integration tests locally, set `SQL_AGENT_CLI_INTEGRATION=1` and provide the documented `SQL_AGENT_CLI_POSTGRES_*` and `SQL_AGENT_CLI_MYSQL_*` environment variables:

```powershell
uv run --locked python -m unittest tests.test_network_integration tests.test_read_only_integration -v
```

## License

MIT
