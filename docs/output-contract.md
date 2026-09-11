# Output and compatibility contract

`sql-agent-cli` separates machine-readable payloads from diagnostics so agents and automation can consume results predictably.

## Query output

JSON is the default query format. Successful output has three stable top-level objects:

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

The `target` object contains the resolved non-secret connection settings. It never includes a password.

The `query` object contains:

| Field | Meaning |
| --- | --- |
| `input` | Query text supplied by the caller |
| `normalized` | Validated query text sent for execution |
| `statement_type` | Validated SQL statement class |

The `result` object contains:

| Field | Meaning |
| --- | --- |
| `columns` | Ordered result column names |
| `rows` | Result values in column order |
| `returned_row_count` | Number of rows included in the payload |
| `truncated` | Whether the configured row limit omitted more rows |

## Value serialization

| Database value | JSON representation |
| --- | --- |
| SQL `NULL` | JSON `null` |
| Date, time, or datetime | ISO 8601 string |
| Decimal | String |
| UUID | String |
| Bytes | Base64 string prefixed with `base64:` |

## Other query formats

The CLI also supports `markdown`, `table`, and `csv` output.

```powershell
uvx sql-agent-cli --format markdown "SELECT ..."
uvx sql-agent-cli --format table "SELECT ..."
uvx sql-agent-cli --format csv "SELECT ..."
```

Markdown and table output include target, statement type, row count, and truncation metadata. CSV output contains only the header and result rows.

## Configuration check output

`config check --format json` emits a complete diagnostic payload even when one or more selected targets fail:

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

Failed result objects use `status: "error"` and include `error.type`, `error.code`, `error.message`, and `error.next_steps`. Raw driver text and configured passwords are not exposed.

Additional fields include `offline`, `config_path`, `credentials_path`, `skill`, `skill_ready`, and `database_ready`. Each result includes `credential_source`, `credential_persistence`, and `verification` when available. Verification distinguishes connection, authentication, read-only session, catalog, and transport identity. An offline check sets `database_ready` to null and verification to not checked. It does not prove authentication or connectivity.

## Setup output

Setup defaults to a human-readable text report. `setup --format json` reports `status`, `database_ready`, `configuration_saved`, and `skill_ready`. Once resolved, it adds paths, target, credential source, verification stages, and skill installation results. Failed setup includes a safe `error` object. A protected skill can cause failure after configuration is saved. Clients must inspect the separate readiness fields.

`--format text` explicitly selects the default text report. Interactive prompts and progress remain on stderr. Argument-parser failures happen before a report is available.

## Stdout and stderr

Stdout is reserved for payload output. Diagnostics and errors go to stderr. Normal query failures emit no stdout payload.

`setup --format json` and `config check --format json` retain diagnostic payloads on failure. Runtime failures return 1. Missing or invalid input returns 2.

Skill synchronization notices and maintenance warnings go to stderr. They do not change the primary command exit status or JSON stdout.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Success |
| `1` | Runtime, connection, driver, timeout, or query-execution failure |
| `2` | Command usage or SQL validation failure |

## Compatibility policy

The supported config schema consists of the documented `[defaults]` and `[targets.NAME]` fields. Unknown fields are ignored when reading and preserved by config-writing commands. Unrelated comments and tables are preserved. Concurrent edits cause a safe failure instead of replacement.

Starting with 1.0.0, the project follows semantic versioning for the documented CLI, config, JSON, stdout, stderr, and exit-code contracts. Additive compatible behavior ships in minor releases. Intended breaking changes require a major release.

When practical, a deprecated interface will warn for at least one minor release before removal. Security fixes may require faster changes and will be called out explicitly.
