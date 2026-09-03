# Changelog

All notable changes to this project are documented here. Starting with 1.0.0, releases follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.13.0] - 2026-09-03

- Added local automatic synchronization of already-installed managed agent skills, using the running CLI version and front matter content hashes to preserve modifications and prevent downgrades.
- Added `skill install --force`, read-only `skill status`, and `skill remove`. Existing `install-skill` and `remove-skill` commands remain supported.
- Excluded local development builds and custom skill locations from automatic synchronization. Skill updates use atomic replacement and stderr notices without changing command results.
- Kept unrelated files when removing a skill and prevented installation from overwriting unmanaged content.

## [0.12.0] - Release candidate

- Added `config check` for safe, structured connectivity diagnostics.
- Added `--about` package metadata.
- Added real PostgreSQL and MySQL integration coverage and broader Python/Windows CI.
- Normalized driver failures into concise exit-code-1 diagnostics without tracebacks or exposed passwords.
- Added read-only PostgreSQL and MySQL/MariaDB session settings and direct integration checks that sessions reject writes.
- Added a SQLite execution timeout while retaining read-only file access.
- Hardened SQL validation against mutating CTEs, locking reads, unsafe PostgreSQL file functions, stacked statements, and non-allowlisted SQLite pragmas.
- Made TLS modes explicit: `required` fails without negotiated encryption, `preferred` may fall back to plaintext, and `disabled` prohibits TLS.
- Added and refined the managed `$sql-agent-cli` skill, including `uvx` invocation, troubleshooting, least-privilege, and TLS guidance.
- Documented the public JSON, config, stream, exit-code, semantic-versioning, deprecation, and security contracts targeted for 1.0.

[Unreleased]: https://github.com/pseudosavant/sql-agent-cli/compare/v0.13.0...HEAD
[0.13.0]: https://github.com/pseudosavant/sql-agent-cli/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/pseudosavant/sql-agent-cli/compare/v0.11.0...v0.12.0
