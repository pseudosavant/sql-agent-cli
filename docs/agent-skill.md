# Managed agent skill

The `$sql-agent-cli` skill teaches coding agents to use the published CLI through `uvx`, start with the configured default target, run bounded read-only SQL, interpret structured output, and preserve credential and TLS safety.

## Install the skill

Run `uvx sql-agent-cli setup` to configure a database and install the skill together. To install just the skill, use this command.

```powershell
uvx sql-agent-cli skill install
```

The primary copy lives at `~/.agents/skills/sql-agent-cli/SKILL.md`. If the Claude Code profile directory `~/.claude` exists, setup and `skill install` also write `~/.claude/skills/sql-agent-cli/SKILL.md`. They create the `skills` folder if needed. With a custom Claude Code profile, set `CLAUDE_CONFIG_DIR` to its absolute path when running the CLI. The same directory check applies to that profile.

Without an existing Claude Code profile, the CLI installs only the primary copy. If you add a Claude Code profile later, run `uvx sql-agent-cli skill install` to add the skill there. This works without a database connection.

If your Claude skills folder or its `sql-agent-cli` entry already links to the primary copy, the CLI manages the shared file once. Removing the skill leaves your link in place.

## Inspect the skill

Read the path, management state, installed and running versions, integrity, and automatic synchronization eligibility without changing files:

```powershell
uvx sql-agent-cli skill status
uvx sql-agent-cli skill status --format json
```

## Automatic synchronization

An installed CLI checks existing copies in each default location before running a query or printing help, version, or about information. It updates unmodified older copies to the bundled version. It preserves current and newer versions, local edits, and unmanaged content. Missing copies are installed only by setup or an explicit skill installation.

Synchronization is local. It does not query a package index, refresh the `uv` cache, or update the CLI. Local source checkouts, local source installations, and editable builds skip automatic synchronization.

`config`, `targets`, and skill-management commands skip automatic synchronization. Setup explicitly installs the skill after verification. Changes affect future skill loading and may not replace instructions already loaded into an active agent session.

## Integrity metadata

The generated skill records lifecycle information in YAML front matter:

```yaml
metadata:
  managed-by: sql-agent-cli
  managed-version: "0.15.0"
  managed-content-sha256: "sha256:<64 lowercase hexadecimal characters>"
```

The version exactly matches `uvx sql-agent-cli --version`. The SHA-256 hash covers the complete UTF-8 text with LF line endings and only the hash value replaced by `""`. It detects modifications. It is not a signature or security boundary.

Legacy HTML management markers remain recognized. Unversioned legacy skills are treated as version 0. Managed skills with missing or malformed versions receive a fresh replacement as a recovery step.

With valid version metadata, modified skills and skills with missing, malformed, or mismatched hashes are preserved. Restore the bundled managed content explicitly with:

```powershell
uvx sql-agent-cli skill install --force
```

Force installation still refuses unmanaged content and never downgrades a newer skill.

The default force command applies to both detected locations. To restore just one copy, add `--skills-dir PATH` and supply its skills root.

## Agent discovery

Start a new session and ask your agent to use the sql-agent-cli skill. In local Claude Code, type `/sql-agent-cli` followed by your request. The personal skill is available across local projects. [Claude Code skill locations](https://code.claude.com/docs/en/skills)

An agent running under another user, on a remote host, or inside a container needs access to the skill and database configuration in that execution environment. Check with `uvx sql-agent-cli config check --offline --format json` there. An offline check does not prove network access or authentication.

Pass `--skills-dir PATH` to `skill install`, `skill status`, or `skill remove` to select a single skills root. For example, `uvx sql-agent-cli skill status --skills-dir /home/alex/.claude/skills` inspects only Alex's Claude Code copy. Skills outside the default locations require explicit updates.

Development builds can install the bundled skill explicitly with `uvx --from . sql-agent-cli skill install`.

## Remove the skill

To remove the managed skill from both default locations, run this command.

```powershell
uvx sql-agent-cli skill remove
```

Removal refuses an unmanaged `SKILL.md` unless `--force` is supplied. Only `SKILL.md` is managed. Unrelated files remain in place. To reinstall a removed copy, run setup or `skill install`.

The `install-skill` and `remove-skill` aliases remain supported for compatibility. All skill commands accept `--format text|json`. Status defaults to text. Installation and removal default to JSON.

## Maintenance behavior

Automatic replacements are atomic and recheck the installed file before replacement. Maintenance failures never change the primary command exit status or JSON stdout. Update notices and preservation warnings go to stderr.
