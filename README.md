# sql-agent-cli

`sql-agent-cli` is a read-only SQL CLI for agentic workflows.

It is designed to run safe, single-statement queries against configured database targets and return deterministic output that tools like Codex CLI and Claude Code can consume reliably.

V1 targets:

- MySQL
- MariaDB
- PostgreSQL
- SQLite

## Status

This repo is currently under active development.

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
sql-agent-cli "SELECT 1"
```

## Primary usage

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

One-off SQLite query without config:

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
sql-agent-cli config set-default-target NAME
sql-agent-cli config add-target NAME [options]
sql-agent-cli config remove-target NAME
sql-agent-cli config init-native-auth --engine postgres [--target NAME]
sql-agent-cli config init-native-auth --engine mysql [--target NAME]
sql-agent-cli targets
```

`config show` displays effective target settings and credential-source hints without revealing secrets.

## Output

Supported formats:

- `json`
- `markdown`
- `table`
- `csv`

Default format:

- `json`

Stdout is reserved for payload output. Diagnostics and errors go to stderr.

## Read-only guarantee

V1 is read-only by design.

Intended allowed statement classes include:

- `SELECT`
- `WITH ... SELECT`
- `SHOW`
- `DESCRIBE` / `DESC`
- `EXPLAIN`

The tool rejects mutating or administrative statements before execution and executes exactly one statement per invocation.

## SSL

Secure defaults are required by default for network databases.

Supported model:

- `--ssl-mode required`
- `--ssl-mode preferred`
- `--ssl-mode disabled`
- `--insecure` as shorthand for `--ssl-mode preferred`

## Development direction

Implementation choices currently targeted by the spec:

- `PyMySQL[rsa]` for MySQL and MariaDB
- `psycopg[binary]` for PostgreSQL
- stdlib `sqlite3` for SQLite
- `sqlglot` for parser-backed SQL validation

## License

MIT
