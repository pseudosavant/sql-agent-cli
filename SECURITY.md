# Security policy

## Supported versions

Until 1.0.0 is released, security fixes are applied to the latest published version only. After 1.0.0, the project will document any broader support window here.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability-reporting flow for this repository when available. If that option is unavailable, contact the maintainer privately through the contact methods on the maintainer's GitHub profile.

Include the affected version, database engine, reproduction steps, impact, and any suggested mitigation. Do not include real credentials, connection strings, or production data.

## Safety model

`sql-agent-cli` is read-only by design and uses several independent controls:

- parser-backed, single-statement SQL validation
- rejection of mutating operations, locking reads, unsafe functions, and non-allowlisted SQLite pragmas
- read-only PostgreSQL and MySQL/MariaDB sessions
- read-only SQLite connections
- row and query-time limits
- secret redaction from public metadata and normalized runtime diagnostics

These controls reduce risk but do not replace database authorization. Use a dedicated database identity with only the minimum read and metadata permissions required. Stored functions, database extensions, server configuration, and privileged roles can expand what apparently read-oriented SQL can do.

For network databases, `ssl_mode = "required"` requires encrypted transport. Certificate authority and hostname verification depend on native PostgreSQL or MySQL client trust configuration. `preferred` may fall back to plaintext and `disabled` prohibits TLS; use either only when that weaker behavior is explicitly accepted.

Never place passwords in command arguments, config committed to source control, logs, issue reports, or agent prompts. Prefer native credential files or environment mechanisms and use `--password-stdin` only when needed.
