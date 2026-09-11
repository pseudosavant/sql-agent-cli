# Release Candidate Spec: `sql-agent-cli` v1

## Purpose

Build a new Python CLI named `sql-agent-cli` that runs safe, read-only SQL queries against local or remote databases and emits deterministic, agent-friendly output.

The tool should follow the same broad product pattern as similar agent-first CLIs such as `confluence-fetch`:

1. local single-file execution via `uv run ./sql_agent_cli.py ...` using PEP 723 inline script metadata
2. packaged execution via `uvx sql-agent-cli ...`
3. agent-first stdout/stderr behavior with stable output contracts

Primary audience:

- agentic coding tools such as Codex CLI and Claude Code

Secondary audience:

- humans running ad hoc database inspection commands directly in a terminal

License:

- MIT

Release status:

- 0.14.0 adds guided setup, saved credentials, and read-only readiness checks while the public contracts are stabilized for 1.0.
- The documented CLI, config, JSON, stdout/stderr, and exit-code behavior in this spec is the intended 1.0 public contract.

---

## Product goals

1. The primary happy path is `sql-agent-cli "SELECT ..."` against a configured default target.
2. The tool supports named targets so multiple databases can be queried safely from one CLI.
3. V1 supports MySQL, MariaDB, PostgreSQL, and SQLite.
4. The internal design leaves room for SQL Server later without redesigning the config model.
5. The tool is strictly read-only in v1.
6. Stdout contains payload only. Diagnostics, warnings, progress, and errors go to stderr only.
7. The default output is optimized for agents, not for humans staring at a terminal.
8. The project is publishable so `uvx sql-agent-cli --help` works.
9. The repo also keeps a root-level `sql_agent_cli.py` wrapper for `uv run`.

## Non-goals

1. Write queries in v1.
2. Interactive TUI behavior.
3. Backward compatibility with `sql-query.py`.
4. Cross-database joins or fan-out queries in a single invocation.
5. Full database administration features.

---

## Naming and packaging

## Public command

`sql-agent-cli`

## Recommended package/module layout

```text
sql-agent-cli/
  pyproject.toml
  README.md
  sql_agent_cli.py
  spec.md
  src/
    sql_agent/
      __init__.py
      cli.py
      config.py
      models.py
      validation.py
      render.py
      engines/
        __init__.py
        base.py
        mysql.py
        postgres.py
        sqlite.py
```

## Packaging requirements

1. Publishable Python package via `pyproject.toml`.
2. Console script entry point exposed as `sql-agent-cli`.
3. Root-level `sql_agent_cli.py` wrapper with PEP 723 metadata for local script execution.
4. The wrapper delegates into packaged implementation instead of duplicating the application logic.

## Dependency recommendations

Recommended implementation dependencies:

1. `PyMySQL[rsa]` for MySQL and MariaDB
2. `psycopg[binary]` for PostgreSQL
3. standard-library `sqlite3` for SQLite
4. `sqlglot` for parser-backed SQL validation

Rationale:

1. these choices are compatible with the `uvx` packaging and execution model
2. `PyMySQL[rsa]` avoids common MySQL authentication friction while remaining easy to ship
3. `psycopg[binary]` avoids common system-library installation issues for PostgreSQL
4. `sqlglot` provides token-aware, dialect-aware SQL parsing suitable for read-only validation

## PEP 723 requirement

`sql_agent_cli.py` must include inline script metadata similar to:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "...",
# ]
# ///
```

Notes:

1. The wrapper may add `src/` to `sys.path` during local development.
2. The package itself still uses `pyproject.toml` metadata for `uv build` and `uv publish`.

---

## Database scope

## Required v1 engines

1. MySQL
2. MariaDB
3. PostgreSQL
4. SQLite

## Design requirement for later engines

The target/config model must be engine-neutral enough that SQL Server can be added later without changing the user-facing target structure.

That means:

1. targets are named records with an explicit `engine`
2. shared concepts such as host, port, database name, SSL mode, and read-only behavior live in common models where practical
3. engine-specific fields may exist where required

---

## Agent-first design principles

1. Payload output goes to stdout only.
2. Diagnostics, warnings, progress, and errors go to stderr only.
3. Resolution rules must be deterministic and documented in `--help`.
4. The default output must be structured enough for agents to consume reliably.
5. Read-only safety must be enforced before any query is executed.
6. Missing target or credential context must fail fast with corrective error text.

---

## Recommended CLI

## Primary fetch shape

Primary happy path:

```text
sql-agent-cli "SELECT ..."
```

Named target:

```text
sql-agent-cli --target reporting "SELECT ..."
```

Explicit query flag:

```text
sql-agent-cli --target reporting --query "SELECT ..."
```

SQL file:

```text
sql-agent-cli --target reporting --sql-file query.sql
```

Stdin:

```text
Get-Content query.sql | sql-agent-cli --target reporting
```

## Supported query input forms

V1 should support exactly one query source per invocation:

1. positional query text
2. `--query SQL`
3. `--sql-file PATH`
4. stdin when no positional query and no query flag/file were provided

If more than one query source is supplied, fail with a usage error.

## Target selection

Resolution order:

1. `--target NAME`
2. an explicit `--engine` builds an ephemeral target without merging `[defaults].target`
3. `[defaults].target`
4. fail with a clear error if no target is available

## One-off connection overrides

The tool should also support one-off execution without config by allowing connection details on the CLI.

Examples:

```text
sql-agent-cli --engine mysql --host db.example.com --database app --user paul "SELECT ..."
sql-agent-cli --engine postgres --host db.example.com --database app --user paul --password-stdin "SELECT ..."
sql-agent-cli --engine sqlite --path C:\data\app.db "SELECT ..."
```

Behavior:

1. CLI connection flags can define a full ephemeral target for the current run.
2. CLI connection flags override the selected config target field-by-field.
3. If no target is specified and no default target exists, a complete ephemeral CLI target is allowed.
4. If the resulting target is incomplete, fail with a clear validation error before attempting a connection.
5. If `--engine` is supplied without `--target`, start from an ephemeral target rather than the configured default target.

## Credential input methods

V1 should avoid password-as-argument by default.

Recommended supported methods:

1. native engine credential resolution
2. `--password-stdin`
3. optional `--prompt-password` for human use

V1 should not require `--password` as a normal CLI argument.

## Recommended commands

```text
sql-agent-cli "SELECT ..."
sql-agent-cli --target NAME "SELECT ..."
sql-agent-cli --about
sql-agent-cli config show
sql-agent-cli config check [--target NAME | --all] [--format text|json]
sql-agent-cli config set-default-target NAME
sql-agent-cli config add-target NAME [options]
sql-agent-cli config remove-target NAME
sql-agent-cli config init-native-auth --engine postgres [--target NAME]
sql-agent-cli config init-native-auth --engine mysql [--target NAME]
sql-agent-cli targets
sql-agent-cli skill install [--force] [--skills-dir PATH]
sql-agent-cli skill status [--format text|json] [--skills-dir PATH]
sql-agent-cli skill remove [--force] [--skills-dir PATH]
sql-agent-cli install-skill
sql-agent-cli remove-skill
```

Compatibility requirement:

- none

## Managed agent skill

`uvx sql-agent-cli skill install` installs or updates the managed `$sql-agent-cli` skill at `~/.agents/skills/sql-agent-cli/SKILL.md`. `install-skill` remains an alias. All skill commands support `--skills-dir PATH` for explicit custom locations and `--format text|json`. Installation and removal retain their default deterministic JSON results. Status defaults to text.

One canonical renderer supplies the existing name, description, instructions, and examples. The skill continues using `uvx sql-agent-cli` with the default-target happy path, read-only boundary, least-privilege roles, structured output, native credentials, TLS behavior, and one-time `config check` troubleshooting. Generation uses UTF-8 without a BOM, LF line endings, and a trailing newline.

Management state lives under the supported YAML `metadata` mapping. `managed-by` is `sql-agent-cli`. `managed-version` is a quoted string exactly matching `sql_agent.__version__`, the runtime version reported by the CLI. `managed-content-sha256` is a quoted `sha256:` value followed by 64 lowercase hexadecimal characters. There is no top-level version or sidecar. Existing unrelated supported template metadata is preserved.

The hash covers the entire normalized UTF-8 file with only the hash field's value replaced by `""`. Verification uses the installed file's original formatting, without YAML reserialization or comparison with the current bundled hash. CRLF and CR normalize to LF. The hash detects modifications and is not a signature.

Query and informational CLI runs, including help, version, about, and rendering, synchronize only an existing managed skill in the standard location. Configuration, targets, and skill-management commands skip this check. Setup explicitly installs the skill after database verification. PEP 440 comparison determines whether the installed version is older, equal, or newer. Equal and newer versions are never automatically replaced. A pristine older version is updated. An older skill with a missing, malformed, or mismatched hash is preserved with a stderr recommendation to run `uvx sql-agent-cli skill install --force`.

The legacy `<!-- managed-by: sql-agent-cli -->` marker remains recognized unless parsed metadata names a different manager. Unversioned legacy skills are treated as version 0. Missing or malformed managed versions trigger a fresh replacement before hash checks. Newly generated skills use only front matter management metadata.

Explicit installation creates missing skills and updates pristine older skills. Current skills are unchanged. Altered or unverifiable managed skills with valid versions require `--force`. Force installation still refuses unmanaged content and never downgrades newer versions. Unexpected directories and unrelated files are preserved.

Automatic synchronization uses installed-distribution metadata to exclude editable and local source builds, including PEP 610 local `file:` sources. Installed wheels remain eligible, including local wheel artifacts. Unidentified runtime sources skip synchronization. Explicit installation still works from local builds. Custom locations require explicit updates. The feature never queries an index, refreshes uv's cache, or updates the CLI.

`skill status` is read-only. Its text and JSON output report the selected path, installation and management state, runtime and installed versions, version comparison, integrity, runtime source, automatic eligibility and skip reason, and an applicable force-install recommendation.

Replacement writes and flushes a temporary file in the target directory, closes it, revalidates the installed state, and uses atomic replacement. Failures clean up temporary files and never affect the primary command's exit status. Notices use stderr exclusively. Updates affect future agent skill loading and may not change a running agent session.

`skill remove` and its `remove-skill` alias remove managed `SKILL.md` files, including legacy skills. Existing `--force` removal of unmanaged content remains supported. Removal preserves unrelated files and removes the skill directory only when empty. A missing skill directory returns the existing `not_installed` JSON result.

---

## Read-only safety model

## Statement policy

V1 must execute exactly one SQL statement per invocation.

Required behavior:

1. allow a single trailing semicolon and strip it before validation
2. reject multiple statements
3. reject empty input

Rationale:

- agents frequently emit a harmless trailing semicolon
- stacked statements materially increase risk and complexity

Implementation requirement:

- validation should be token-aware or parser-backed rather than a simple regex-only keyword scan

Recommended implementation:

- use `sqlglot` with the selected engine dialect and require exactly one parsed statement

## Allowed statement classes

V1 should allow only read-oriented statements.

Required allowlist:

1. `SELECT`
2. `WITH ... SELECT`
3. `SHOW`
4. `DESCRIBE` and `DESC`
5. `EXPLAIN`

Engine-specific notes:

1. SQLite may also allow read-only `PRAGMA` statements if they are explicitly validated as non-mutating.
2. `USE` must not be allowed. Target selection belongs to CLI/config, not SQL text.
3. Stored procedures, dynamic SQL, and administrative commands must not be allowed.

## Disallowed behavior

The validator should reject statements containing or invoking mutating or administrative behavior, including but not limited to:

1. `INSERT`
2. `UPDATE`
3. `DELETE`
4. `REPLACE`
5. `MERGE`
6. `UPSERT`
7. `DROP`
8. `ALTER`
9. `CREATE`
10. `TRUNCATE`
11. `GRANT`
12. `REVOKE`
13. `LOCK`
14. `UNLOCK`
15. `CALL`
16. `EXEC`
17. `PREPARE`
18. `DEALLOCATE`
19. `SET`
20. `LOAD_FILE`
21. `INTO OUTFILE`
22. `INTO DUMPFILE`
23. sleep/benchmark-style functions

Validation must happen before a database connection is attempted where practical.

Validation requirements:

1. comments must not confuse statement classification
2. quoted strings and identifiers must not be treated as executable keywords
3. semicolons inside quoted literals must not be treated as statement separators
4. CTE-based `WITH ... SELECT` queries must be accepted when they remain read-only
5. parse failures should return a clear validation error instead of falling through to execution

## Connection-level read-only behavior

Where the underlying driver/database supports a true read-only connection mode, the tool should enable it.

Examples:

1. SQLite connections must be opened in read-only mode.
2. PostgreSQL connections must set `default_transaction_read_only=on` for the session.
3. MySQL/MariaDB connections must run `SET SESSION TRANSACTION READ ONLY` before user SQL.
4. Integration tests must bypass parser validation and prove that PostgreSQL and MySQL reject a write at the session layer.
5. These controls are defense in depth. Targets should use dedicated database identities with only the required read and metadata privileges.
6. Stored functions, extensions, temporary objects, server configuration, and privileged database roles can weaken assumptions made from SQL text alone.

---

## Output contract

## Formats

V1 should support:

1. `json`
2. `markdown`
3. `table`
4. `csv`

## Default format

Default output format:

- `json`

Rationale:

- JSON is the best default for agents because it can carry structured metadata, truncation status, and stable field names.

## JSON shape

Recommended top-level structure:

```json
{
  "target": {
    "name": "default",
    "engine": "mysql",
    "database": "app",
    "host": "db.example.com"
  },
  "query": {
    "input": "SELECT id, name FROM users",
    "normalized": "SELECT id, name FROM users",
    "statement_type": "select"
  },
  "result": {
    "columns": ["id", "name"],
    "rows": [
      [1, "Alice"],
      [2, "Bob"]
    ],
    "returned_row_count": 2,
    "truncated": false
  }
}
```

The top-level `target`, `query`, and `result` objects and the documented fields above are stable public fields. Compatible releases may add fields, but must not remove or reinterpret existing fields without a major version change. The payload includes:

1. resolved target metadata with secrets omitted
2. normalized query text
3. column names
4. rows
5. returned row count
6. truncation indicator

Serialization requirements:

1. datetimes and dates should be emitted as ISO 8601 strings
2. decimals should be emitted as strings to avoid silent precision loss
3. bytes/blob values should be emitted as base64 strings or explicit placeholders; the choice should be documented and stable
4. `NULL` should become JSON `null`
5. UUID-like values may be emitted as strings

Row count semantics:

1. use `returned_row_count` for the number of rows actually present in the payload
2. use `truncated` to indicate whether rows were omitted due to `max_rows`
3. do not claim a `total_row_count` unless the tool actually computed it

## Markdown/table behavior

1. `markdown` should render a stable prompt-friendly table plus a short metadata header.
2. `table` should optimize for terminal readability.
3. `csv` should emit raw CSV payload only, with no explanatory text on stdout.

## Stream behavior

1. Successful query payloads are written to stdout.
2. Diagnostics and errors are written to stderr.
3. Failed normal queries do not emit partial stdout payloads.
4. `setup` and `config check --format json` emit diagnostic reports even on failure. Runtime failures return 1. Missing or invalid input returns 2.

---

## Config model

## Config path

Use a single user config file at:

```text
~/.sql-agent-cli/config.toml
```

## Default target design

The default target should be an alias, not a duplicated block of connection data.

Recommended shape:

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

[targets.local_sqlite]
engine = "sqlite"
path = "C:/data/app.db"
```

This is the preferred approach because:

1. `[defaults].target` clearly selects the default target
2. all connection details live in exactly one place
3. renaming or editing a target does not require syncing duplicated settings

## First-time setup

`sql-agent-cli setup [RESOURCE]` verifies a database, saves its named target, and installs the managed skill in `~/.agents/skills/sql-agent-cli/SKILL.md`. No agent-specific copy or link is created. Existing skill integrity, unmanaged-content, and newer-version protections remain unchanged.

RESOURCE is a password-free PostgreSQL, MySQL, or MariaDB URL, or an existing SQLite path. Network setup resolves identity from the resource, explicit connection flags, the selected target, and native connection sources before asking for missing fields. The first target is named default unless specified. Adding a named target preserves an existing default. SQLite paths are saved absolutely.

Setup accepts `--target`, existing connection flags, `--auth auto|native|managed`, `--auth-file`, `--service`, `--unix-socket`, `--ssl-ca`, `--replace-password`, `--non-interactive`, `--config`, `--credentials-file`, and `--format json|text`. Text is the default. Automation can select JSON explicitly with `--format json`. Interactive input requires terminal stdin and stderr. Password input uses asterisk masking and no history. Prompts, progress, and errors go to stderr. Non-interactive setup never reads stdin or prompts.

Verification uses one read-only session for a constant query and a bounded catalog query. It reports authentication, session mode, catalog visibility, and transport identity. No application rows or write probes are used. Table access, absence of write privileges, future validity, and access from another execution environment remain unverified. SQLite WAL sidecars and server audit events can still occur.

Verification failures preserve existing local state. Success saves configuration and entered credentials before installing the skill. Skill failures report the saved database configuration separately. Repeated setup preserves unrelated targets, unknown configuration fields, comments, and credentials. Optimistic snapshot checks prevent overwriting concurrent configuration edits.

## Secret storage policy for v1

Ordinary configuration contains no managed passwords. Entered setup passwords are stored in a separate credentials.toml beside the selected configuration file. Files use normal inherited permissions and the process umask. No keyring or custom restrictive ACL is required. Native PostgreSQL password-file rules still apply to native files.

Credentials have opaque identifiers and bind to the engine, host, port, database, login, socket, and service. Replacements are verified, staged under new identifiers, and selected by an atomic configuration update. Failed saves clean up the staged entry. Unused older entries are removed after a successful switch. A process crash can leave an unused entry.

Passwords never appear in command arguments, public configuration, prompts in agent chat, or diagnostics. Driver exceptions are classified without echoing raw messages. Environment secrets are never persisted. Short-lived tokens are acquired and refreshed externally.

Configuration path precedence is `--config`, `SQL_AGENT_CLI_CONFIG`, then ~/.sql-agent-cli/config.toml. Credential path precedence is `--credentials-file`, `SQL_AGENT_CLI_CREDENTIALS_FILE`, then credentials.toml beside the selected config. Paths must differ.

## Target shape

Recommended target fields:

Shared fields:

1. `engine`
2. `database`
3. `user`
4. `host`
5. `port`
6. `ssl_mode`
7. `max_rows`
8. `connect_timeout_seconds`
9. `query_timeout_seconds`

SQLite-specific fields:

1. `path`

Notes:

1. `ssl_mode` should default to secure behavior where relevant.
2. SQLite ignores network-only fields.
3. The implementation may allow additional engine-specific fields later.
4. Engine-specific credential hints may be stored in config, but raw passwords should not be the preferred v1 path.

## Native credential support

PostgreSQL retains libpq authentication, including PGPASSFILE, PGPASSWORD, PGSERVICE, and PGSERVICEFILE. Missing connection fields may come from the selected service or PG environment. Password-file hints follow libpq's Windows APPDATA location and Unix ~/.pgpass location. Service discovery checks the user file or PGSERVICEFILE and an explicit PGSYSCONFDIR.

MySQL and MariaDB retain client option files. A target can select an auth_file. Otherwise the existing APPDATA/MySQL/.my.cnf and ~/.my.cnf search order remains. Connection fields, including native port and socket, are resolved before built-in defaults. mysql_config_editor login paths remain unsupported.

`--password-stdin` and `--prompt-password` remain supported for queries and single-target checks. Offline checks and --all reject password-input flags. Native authentication is also available through `--auth native` when a saved credential should be bypassed.

## SSL behavior

Required behavior:

1. new network targets created by setup default to `ssl_mode = "verify-full"`
2. existing targets retain their mode and direct legacy connections default to `required`
3. support a per-target config value for `ssl_mode`
4. support a shared user-facing `ssl_mode` model of `verify-full`, `required`, `preferred`, or `disabled`
5. map the shared `ssl_mode` values onto engine-specific driver options internally
6. support an explicit CLI `--ssl-mode {verify-full,required,preferred,disabled}` override
7. support a CLI `--insecure` shorthand that means `--ssl-mode preferred`
8. support an explicit less-secure override when needed, such as `--ssl-mode disabled`
9. `--insecure` should not modify config permanently
10. `required` must fail if encrypted transport is not negotiated
11. `preferred` attempts TLS but permits a plaintext fallback
12. `disabled` prohibits TLS
13. `required` guarantees transport encryption without guaranteeing server identity
14. `verify-full` requires certificate and hostname verification for TCP connections. `ssl_ca` selects a private CA file

Rationale:

- SSL behavior is a target attribute, but `--insecure` is a useful temporary override for troubleshooting

## Config commands

Recommended commands:

```text
sql-agent-cli config show
sql-agent-cli config check [--target NAME | --all] [--format text|json]
sql-agent-cli config set-default-target NAME
sql-agent-cli config add-target NAME --engine mysql --host HOST --port 3306 --database DB --user USER
sql-agent-cli config add-target NAME --engine postgres --host HOST --port 5432 --database DB --user USER
sql-agent-cli config add-target NAME --engine sqlite --path PATH
sql-agent-cli config remove-target NAME
sql-agent-cli config init-native-auth --engine postgres [--target NAME]
sql-agent-cli config init-native-auth --engine mysql [--target NAME]
```

Behavior requirements:

1. `config show` displays the effective defaults and configured targets with credential sources described but secrets redacted
2. `config show` should indicate whether each target is complete enough to run
3. `config check` defaults to the configured default target, `--target NAME` checks one named target, and `--all` is required to contact every configured target
4. `config check` uses the setup verifier without saving files or synchronizing skills. It supports --offline, source diagnostics, text, and JSON output
5. `config check --all` continues after individual failures and returns exit code `1` if any selected target fails
6. normal queries do not require a successful preflight check
7. config-writing commands create the file if it does not exist
8. only documented config fields are supported; unknown fields are ignored on read and may be removed when a config-writing command rewrites the file

## Native auth template helpers

V1 should help users bootstrap native credential files without requiring them to remember the exact file format.

Recommended command:

```text
sql-agent-cli config init-native-auth --engine postgres [--target NAME]
sql-agent-cli config init-native-auth --engine mysql [--target NAME]
```

Recommended behavior:

1. create the standard file only if it does not already exist, unless an explicit overwrite flag is later added
2. write a commented template with placeholders and brief inline instructions
3. print the exact path written and the next step for the user
4. never auto-fill or persist secrets unless the user explicitly supplied them for that purpose
5. for PostgreSQL, set restrictive file permissions where required and warn if that cannot be done
6. if the file already exists, do not modify it silently; print a clear message instead
7. when `--target NAME` is supplied, prefill non-secret fields such as host, port, database, and user from the selected target where those fields are relevant

PostgreSQL helper requirements:

1. seed a `.pgpass` template in the preferred platform-specific user location
2. include the expected `hostname:port:database:username:password` record shape
3. explain that PostgreSQL expects restrictive permissions on the file
4. when `--target NAME` is supplied, prefill host, port, database, and user from the target and leave password blank

MySQL helper requirements:

1. seed a `~/.my.cnf` template or platform-appropriate user option file
2. include a minimal `[client]` example section with host, user, password, and optional port
3. note that the file stores plaintext secrets and should be protected with filesystem permissions
4. when `--target NAME` is supplied, prefill host, port, and user from the target and leave password blank

Platform-specific path rules:

1. on Windows, PostgreSQL lookup and template helpers should support both `%APPDATA%\postgresql\pgpass.conf` and `~/.pgpass`
2. on Windows, MySQL lookup and template helpers should support both `%APPDATA%\MySQL\.my.cnf` and `~/.my.cnf` if practical
3. when both locations exist, prefer the explicit native platform path first, then the `~/` fallback
4. when seeding a new file, prefer the native platform path unless the user later requests an explicit path override

Rationale:

- this reduces setup friction without inventing a proprietary credential format
- it keeps the tool aligned with native client conventions
- it avoids requiring users to search for file syntax documentation during setup

---

## Resolution and precedence

## Target field precedence

Recommended precedence:

1. explicit CLI connection flags such as `--host`, `--database`, `--user`, `--path`, `--engine`, `--max-rows`, `--connect-timeout-seconds`, `--query-timeout-seconds`, `--insecure`
2. selected target from `--target NAME`
3. default target named by `[defaults].target`
4. global defaults from `[defaults]` for non-connection behavior such as output format and row limits

Password precedence is explicit `--password-stdin` or `--prompt-password`, then SQL_AGENT_CLI_PASSWORD, then PostgreSQL PGPASSWORD, then the referenced managed credential, then native driver authentication. Explicit native mode skips the managed file. Unavailable referenced credentials fail without silently changing sources. Explicit environment input can bypass an unavailable file.

Connection fields use explicit CLI values, then selected target fields, then native fields, then built-in defaults. Target selection uses --target, then a one-off --engine, then SQL_AGENT_CLI_TARGET, then the configured default.

## Query source precedence

Only one query source may be provided. If more than one is supplied, fail.

Accepted sources:

1. positional query
2. `--query`
3. `--sql-file`
4. stdin

## Format precedence

1. `--format`
2. `[defaults].format`
3. built-in default `json`

---

## Guardrails

## Row limits and timeouts

V1 should enforce sensible default guardrails, overridable by config or CLI.

Recommended defaults:

1. `max_rows = 200`
2. `connect_timeout_seconds = 8`
3. `query_timeout_seconds = 15`

Behavior:

1. results are truncated to `max_rows`
2. truncation is reflected in output metadata
3. timeout failures return a stable non-zero exit code and a concise stderr error

## Exit codes

Public contract:

1. `0` success
2. `1` runtime, connection, driver, or query execution failure
3. `2` usage or validation error

## Versioning and deprecation

Starting with 1.0.0, semantic versioning applies to the documented command line, config schema, JSON fields, stdout/stderr behavior, and exit codes.

1. patch releases contain compatible fixes and security updates
2. minor releases may add backward-compatible commands, options, config fields, and JSON fields
3. intended incompatible changes require a major release
4. when practical, deprecated interfaces should warn for at least one minor release before removal
5. urgent security fixes may require faster changes and must be called out explicitly in release notes

---

## Engine-specific notes

## MySQL and MariaDB

Requirements:

1. use a mature driver with TLS support
2. support host, port, database, and user
3. apply secure SSL defaults unless `--insecure` is used
4. support native option-file based credential resolution where practical
5. do not document or guarantee `MYSQL_PWD` as a public credential source

## PostgreSQL

Requirements:

1. use a mature driver that aligns with libpq-style connection behavior where practical
2. support host, port, database, and user
3. support standard PostgreSQL credential conventions such as `.pgpass`
4. support secure SSL defaults unless explicitly overridden
5. map shared `ssl_mode` values onto PostgreSQL driver-specific SSL settings internally

## SQLite

Requirements:

1. accept a filesystem path instead of host/port/database
2. open the database in read-only mode where possible
3. document any platform-specific path handling in `--help`

---

## Publishing workflow

The project should include a release workflow suitable for public PyPI publishing.

Release expectations:

1. `uv build --no-sources`
2. publish via `uv publish` or a GitHub Actions workflow
3. prefer PyPI Trusted Publishing over long-lived API tokens
4. run no-network tests on supported Python versions
5. run opt-in integration tests against real PostgreSQL and MySQL service containers before building release artifacts

---

## README expectations

The README should include:

1. quick start for the default target model
2. one-off CLI-only connection examples
3. config file examples for MySQL, PostgreSQL, and SQLite
4. examples showing `.pgpass`, MySQL option files, `config init-native-auth --target NAME`, and `--password-stdin`
5. a clear statement that v1 is read-only by design
6. `config check` troubleshooting and `--about` metadata examples

---

## Deferred items

These are intentionally out of scope for initial v1 unless implementation is unexpectedly cheap:

1. SQL Server support
2. OS keychain-backed credential storage
3. write-enabled mode
4. connection profile inheritance
5. schema introspection commands beyond what the query interface already allows
6. MySQL login-path support via `.mylogin.cnf`
