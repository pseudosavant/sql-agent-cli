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

Failed result objects use `status: "error"` and add `error.type` and `error.message`. Configured passwords are not exposed.

## Stdout and stderr

Stdout is reserved for payload output. Diagnostics and errors go to stderr. Normal query failures emit no stdout payload.

`config check --format json` is the intentional exception. It writes its complete diagnostic payload to stdout and returns exit code `1` when any selected target fails.

Skill synchronization notices and maintenance warnings go to stderr. They do not change the primary command exit status or JSON stdout.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Success |
| `1` | Runtime, connection, driver, timeout, or query-execution failure |
| `2` | Command usage or SQL validation failure |

## Compatibility policy

The supported config schema consists of the documented `[defaults]` and `[targets.NAME]` fields. Unknown fields are ignored when reading and may be removed by config-writing commands. Do not use the config file as an extension store.

Starting with 1.0.0, the project follows semantic versioning for the documented CLI, config, JSON, stdout, stderr, and exit-code contracts. Additive compatible behavior ships in minor releases. Intended breaking changes require a major release.

When practical, a deprecated interface will warn for at least one minor release before removal. Security fixes may require faster changes and will be called out explicitly.
