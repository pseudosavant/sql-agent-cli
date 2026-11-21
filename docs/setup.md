# Setup and authentication

Run `uvx sql-agent-cli setup` in a terminal. Setup is interactive only when stdin and stderr are terminals. It never requests credentials in agent chat.

## Connection details

SQLite needs an existing readable database file. Setup saves an absolute path so later working directories do not matter.

Network targets need an engine, server, database, and login. Ports default to 5432 for PostgreSQL and 3306 for MySQL or MariaDB. A password-free URL supplies these values. Percent-encode URL components when needed. Setup rejects embedded passwords, unknown query options, and conflicting connection flags without echoing the URL.

```text
uvx sql-agent-cli setup "postgres://reader@db.example.com:5432/app"
uvx sql-agent-cli setup --engine mysql --host db.example.com --database app --user reader
uvx sql-agent-cli setup --engine postgres --service reporting --auth native
uvx sql-agent-cli setup --engine mysql --auth-file PATH --auth native
```

PostgreSQL service settings and `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER` fill missing fields. MySQL or MariaDB client option files can supply connection fields and a CA path. Verified TLS also retains native client certificate and key settings. Use absolute certificate paths in native files. Explicit flags and saved target fields take precedence. Native discovery reads only the selected file. It does not scan accounts or servers. The [libpq service file](https://www.postgresql.org/docs/current/libpq-pgservice.html) explains native PostgreSQL selection.

New setup targets default to `--ssl-mode verify-full`. Supply `--ssl-ca PATH` for a private CA. Existing targets retain their TLS mode. Unix socket connections have no TLS identity verification. Choose the socket and authentication method explicitly when using local native authentication.

## A terminal session

This example uses a new PostgreSQL login with no existing native credential. The password shown is an asterisk mask. Paths depend on the current user and platform. Prompts and verification messages go to stderr. The completion report goes to stdout.

```text
> uvx sql-agent-cli setup postgres://reader@db.example.com/app

Use a dedicated database login with read access. Ask the database owner to create it if needed.

Required: CONNECT on this database, USAGE on the selected schemas, and SELECT on the tables or views you need.
Passwords expire only when the login or service policy sets an expiry. Renew with the database owner.

https://www.postgresql.org/docs/current/sql-grant.html
https://www.postgresql.org/docs/current/sql-createrole.html

The password is masked with asterisks. It will be saved only after verification.
Use environment credentials for short-lived cloud tokens. Setup does not refresh them.

Separate credentials file:
  /home/alex/.sql-agent-cli/credentials.toml

This file uses normal inherited permissions. Anyone who can read it can use the saved password.

Password: ************

Verifying the connection and a bounded catalog query...

Connection, read-only session, and catalog query verified.
Application table access and the account's write privileges were not tested.
Setup complete.

Configuration:
  /home/alex/.sql-agent-cli/config.toml

Credentials:
  /home/alex/.sql-agent-cli/credentials.toml

Managed skill:
  /home/alex/.agents/skills/sql-agent-cli/SKILL.md
  /home/alex/.claude/skills/sql-agent-cli/SKILL.md

Database verified: true
Configuration saved: true
Skill ready: true

Credential source: input
Credential persistence: file

Start a new agent session. Ask it to use the sql-agent-cli skill to list tables and explain their columns.
```

## Permissions and lifetime

Ask the database owner for a dedicated login. Setup does not create users or grant privileges.

| Service | Access needed for useful reads | Creation and renewal |
| --- | --- | --- |
| PostgreSQL | `LOGIN`, `CONNECT` on the database, `USAGE` on the required schemas, and `SELECT` on the required tables or views | An administrator creates the login. Object owners or authorized grantors provide access. `VALID UNTIL` can expire its password. Without an expiry, PostgreSQL does not impose a fixed password lifetime. |
| MySQL | `SELECT` on the required tables or views. The account's host rule must allow the execution host. `SHOW VIEW` is needed for view definitions, not ordinary table reads. | An administrator creates the account and grants access. Password expiry and account locking depend on account and server policy. |
| MariaDB | `SELECT` on the required tables or views. The account and authentication plugin must allow this host and client. | An administrator creates the account and grants access. Password lifetime depends on the account and server policy. Native socket authentication can be appropriate for local execution. |
| SQLite | Filesystem read access to the database and any required WAL files | No server login or password renewal. Use a valid snapshot when the live database is unavailable. |

These requirements follow the official [PostgreSQL role](https://www.postgresql.org/docs/current/sql-createrole.html) and [grant](https://www.postgresql.org/docs/current/sql-grant.html) documentation, [MySQL account creation](https://dev.mysql.com/doc/refman/8.4/en/create-user.html) and [privileges](https://dev.mysql.com/doc/refman/8.4/en/privileges-provided.html), and [MariaDB account creation](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/create-user).

Do not use an owner, superuser, or write-capable application account when a scoped reader is sufficient. Avoid unnecessary `INSERT`, `UPDATE`, `DELETE`, DDL, administrative privileges, and grant options. Existing broad memberships or grants to `PUBLIC` can still confer access. Future objects may need separate grants. Row security and view permissions may further limit results.

Prefer a durable reader password for convenient local setup, or keep your existing native authentication. Follow the administrator's rotation policy. Renew an expired password outside this read-only tool, then run `uvx sql-agent-cli setup --target reporting --replace-password` in your terminal. Setup verifies the replacement before changing its reference.

Cloud identity tokens should come from the execution environment through `SQL_AGENT_CLI_PASSWORD`, `PGPASSWORD`, or password stdin. Acquire and refresh them externally. Setup cannot identify every token format and does not refresh tokens or renew a terminal login for an agent. Do not save a short-lived token as a durable setup password.

## Storage and precedence

Ordinary configuration contains named targets and an opaque credential reference. Passwords entered during setup go into a separate TOML file. No keyring is required. Files use normal inherited filesystem permissions and the current umask on Unix. No custom ACLs or current-user-only permissions are applied to managed configuration or credentials.

The credentials file contains plaintext secrets. Anyone with read access, including backup or sync software, can obtain them. Choose a location appropriate to the database access it grants. Keep it outside the repository. Native PostgreSQL password files are different. libpq ignores them on Unix if group or world permissions are present. Windows uses `%APPDATA%/postgresql/pgpass.conf`. The existing native template helper retains libpq's required Unix permission handling. See [the PostgreSQL password file](https://www.postgresql.org/docs/current/libpq-pgpass.html).

Password precedence is:

1. Explicit query or check input through `--password-stdin` or `--prompt-password`
2. `SQL_AGENT_CLI_PASSWORD`
3. `PGPASSWORD` for PostgreSQL
4. The target's referenced managed credential
5. Native driver authentication

`--auth native` skips the managed credential, while explicit input and environment overrides still apply. `--auth managed` requires a saved credential or an interactive setup password. `--auth auto` reuses available sources. A corrupt, missing, or mismatched referenced credential fails instead of silently selecting another account. An explicit environment override can bypass an unavailable managed file. `MYSQL_PWD` is not a supported public source.

Managed credentials are bound to the engine, server, port, database, login, and selected socket or service. Changing connection identity requires a new verified credential. Setup stages replacements under new identifiers, switches the configuration reference atomically, then removes the unused older entry. Concurrent configuration edits are detected. Failed verification preserves existing configuration, credentials, and skill content. A process crash can leave an unused entry, but does not switch the target to an unverified password.

When an existing automatic or managed credential is rejected in an interactive setup, setup offers one masked replacement attempt. Explicit native mode and environment overrides do not silently switch to managed credentials.

Setup never saves password environment variables or modifies shell profiles. With an environment override, the report identifies that external source. Success verifies that override, not any shadowed saved password.

## Multiple targets and execution environments

The first successful setup creates `default`. `--target NAME` creates or updates another target. Adding a target preserves an existing default and unrelated settings, comments, and credentials. Repeat setup reuses the selected target. Use `config set-default-target NAME` to change the default.

Path precedence is an explicit flag, then its environment variable, then the default:

| Flag | Environment variable | Default |
| --- | --- | --- |
| `--config PATH` | `SQL_AGENT_CLI_CONFIG` | `~/.sql-agent-cli/config.toml` |
| `--credentials-file PATH` | `SQL_AGENT_CLI_CREDENTIALS_FILE` | `credentials.toml` beside the selected config |
| `--target NAME` | `SQL_AGENT_CLI_TARGET` | The configured default target |

Query `--engine` retains its existing one-off behavior and bypasses default target selection. Setup uses `--target` to select a persistent target.

Include the same path options in recovery commands when using custom files.

Paths and environment variables belong to the process that runs the CLI. A terminal, sandbox, container, remote host, and agent can have different homes, mounts, users, network routes, and inherited variables. Run setup in the agent's environment or provide mounted paths for its configuration and credentials. Setup installs the skill in `~/.agents/skills`. If a Claude Code profile exists, setup installs a second copy in its `skills` folder and creates that folder if needed. The default profile is `~/.claude`. If you use `CLAUDE_CONFIG_DIR`, give the CLI the same absolute profile path.

For automation, use `setup --non-interactive --format json` with complete connection details and a saved, native, or environment credential. It never prompts or consumes stdin. Missing information fails with exit code 2 before saving anything. Use `--auth native` explicitly for passwordless native authentication. Secrets must be supplied by the host's credential mechanism, not literal command arguments.

## Verification and recovery

Setup opens one read-only session, runs a constant query, and inspects up to 21 catalog entries. It reports at most 20 visible relations plus a flag for additional entries. It does not fetch application rows, invoke application views, probe writes, or mark records as read. It checks SQLite's catalog so a plain text file cannot pass merely because `SELECT 1` succeeds.

Success establishes connectivity, authentication, the read-only session, and catalog access at that moment. It reports TLS encryption and whether server identity was verified. It does not prove application table access, absence of write privileges, future credential validity, or access from another process. An empty catalog is a notice, not proof of missing permissions.

Connections can still produce server logs, counters, and authentication audit events. Read-only SQLite WAL access may require existing sidecars or create them when directory permissions allow. Setup does not apply `immutable=1` to a live database. See [SQLite WAL considerations](https://sqlite.org/wal.html).

`config check` performs the same verification without saving files or synchronizing skills. `config check --offline` inspects local readiness without connecting. It reports authentication and network verification as not checked. Missing native credentials cannot always be determined offline. `config show`, bare `config`, `targets`, and `skill status` are also read-only inspections.

| Failure | Recovery |
| --- | --- |
| No target selected | `uvx sql-agent-cli setup` |
| Unknown target | `uvx sql-agent-cli targets --format json` |
| Missing or expired saved password | Renew it if needed, then `uvx sql-agent-cli setup --target reporting --replace-password` |
| Environment credential failed | Replace the credential in the execution environment, then `uvx sql-agent-cli config check --target reporting` |
| Private CA not trusted | Confirm the hostname and CA, then `uvx sql-agent-cli setup --target reporting --ssl-ca PATH` |
| Database unavailable | Restore network or file access, then `uvx sql-agent-cli config check --target reporting` |
| Table access denied | Ask the owner for the required read grants, then retry the original query |
| Skill modified | Review the local edit. Restore the bundled managed skill explicitly with `uvx sql-agent-cli skill install --force` |

If skill installation fails after database configuration succeeds, setup reports `configuration_saved: true` and `skill_ready: false`. Existing unmanaged, modified, or newer skill protections remain in force. Credentials are never printed. Runtime driver text is classified into safe errors rather than echoed.

Setup defaults to readable text. Use `--format json` for a structured report in scripts and automation. Setup and JSON configuration checks retain their diagnostic payload on failure. Query failures produce no stdout payload.
