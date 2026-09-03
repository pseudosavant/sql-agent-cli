# Managed agent skill

The `$sql-agent-cli` skill teaches coding agents to use the published CLI through `uvx`, start with the configured default target, run bounded read-only SQL, interpret structured output, and preserve credential and TLS safety.

## Install the skill

Install or update the user-scoped skill:

```powershell
uvx sql-agent-cli skill install
```

The standard location is `~/.agents/skills/sql-agent-cli/SKILL.md`.

An explicit install creates a missing skill, updates a pristine older skill, and leaves a current skill unchanged. It refuses to overwrite unmanaged content or downgrade a newer skill.

## Inspect the skill

Read the path, management state, installed and running versions, integrity, and automatic synchronization eligibility without changing files:

```powershell
uvx sql-agent-cli skill status
uvx sql-agent-cli skill status --format json
```

## Automatic synchronization

Normal invocations of an installed CLI check an already-installed managed skill in the standard location. The running CLI version is the authority.

An older, unmodified skill is replaced with the bundled skill. Equal or newer versions are left alone. Missing skills are not installed automatically. Unmanaged content is never overwritten.

Synchronization is local. It does not query a package index, refresh the `uv` cache, or update the CLI. Local source checkouts, local source installations, and editable builds skip automatic synchronization.

Skill-management commands also skip automatic synchronization. Changes affect future skill loading and may not replace instructions already loaded into an active agent session.

## Integrity metadata

The generated skill records lifecycle information in YAML front matter:

```yaml
metadata:
  managed-by: sql-agent-cli
  managed-version: "0.13.0"
  managed-content-sha256: "sha256:<64 lowercase hexadecimal characters>"
```

The version exactly matches `uvx sql-agent-cli --version`. The SHA-256 hash covers the complete UTF-8 text with LF line endings and only the hash value replaced by `""`. It detects modifications. It is not a signature or security boundary.

Legacy HTML management markers remain recognized. Unversioned legacy skills are treated as version 0. Managed skills with missing or malformed versions receive a fresh replacement as a recovery step.

With valid version metadata, modified skills and skills with missing, malformed, or mismatched hashes are preserved. Restore the bundled managed content explicitly with:

```powershell
uvx sql-agent-cli skill install --force
```

Force installation still refuses unmanaged content and never downgrades a newer skill.

## Custom skill locations

All skill commands accept `--skills-dir PATH`:

```powershell
uvx sql-agent-cli skill install --skills-dir C:\agent-skills
uvx sql-agent-cli skill status --skills-dir C:\agent-skills
```

Custom locations require explicit updates. Automatic synchronization only inspects the standard location.

Explicit installation also works from development builds:

```powershell
uvx --from . sql-agent-cli skill install
```

## Remove the skill

Remove the managed skill with:

```powershell
uvx sql-agent-cli skill remove
```

Removal refuses an unmanaged `SKILL.md` unless `--force` is supplied. Only `SKILL.md` is managed. Unrelated files remain in place.

The `install-skill` and `remove-skill` aliases remain supported for compatibility. All skill commands accept `--format text|json`. Status defaults to text. Installation and removal default to JSON.

## Maintenance behavior

Automatic replacements are atomic and recheck the installed file before replacement. Maintenance failures never change the primary command exit status or JSON stdout. Successful updates and preservation warnings go to stderr.
