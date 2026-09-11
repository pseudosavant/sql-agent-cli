from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import tomlkit

from .config import config_path
from .errors import ConfigurationError, CredentialError, setup_command
from .models import Target
from .storage import atomic_write, locked, read_bytes


@dataclass(frozen=True)
class Credential:
    password: str | None = field(repr=False)
    source: str

    @property
    def persistence(self) -> str:
        return (
            "file"
            if self.source == "managed_file"
            else "external"
            if self.source not in {"input", "not_required"}
            else self.source
        )


def credentials_path(
    path: Path | None = None, configuration: Path | None = None
) -> Path:
    resolved = (
        (
            path
            or Path(
                os.getenv("SQL_AGENT_CLI_CREDENTIALS_FILE")
                or config_path(configuration).with_name("credentials.toml")
            )
        )
        .expanduser()
        .absolute()
    )
    if resolved.resolve() == config_path(configuration).resolve():
        raise CredentialError("Configuration and credentials must use separate files.")
    return resolved


def environment_credential(engine: str | None) -> Credential | None:
    for name in (
        "SQL_AGENT_CLI_PASSWORD",
        "PGPASSWORD" if engine == "postgres" else "",
    ):
        if name and os.getenv(name):
            return Credential(os.environ[name], name)
    return None


def binding(target: Target) -> dict:
    return {
        "engine": target.engine or "",
        "host": (target.host or "").lower(),
        "port": target.port or 0,
        "user": target.user or "",
        "database": target.database or "",
        "unix_socket": target.unix_socket or "",
        "service": target.service or "",
    }


def _read(path: Path):
    try:
        original = read_bytes(path)
        document = (
            tomlkit.parse(original.decode("utf-8-sig"))
            if original
            else tomlkit.document()
        )
        entries = document.get("credentials", {})
        if not isinstance(entries, dict):
            raise TypeError
        for entry in entries.values():
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("password"), str)
                or not entry["password"]
                or not isinstance(entry.get("binding"), dict)
            ):
                raise ValueError
        return document, original
    except (ValueError, TypeError, ConfigurationError):
        raise CredentialError(
            f"Cannot read credentials. Check access and TOML syntax:\n  {path}\nUse SQL_AGENT_CLI_PASSWORD in this execution environment to bypass this file. Contents were not displayed."
        ) from None


def resolve_credential(target: Target, path: Path | None = None) -> Credential:
    if target.engine == "sqlite":
        return Credential(None, "not_required")
    if target.password is not None:
        if not target.password:
            raise CredentialError(
                "An empty password was supplied. Choose native authentication for a passwordless connection."
            )
        return Credential(target.password, "input")
    override = environment_credential(target.engine)
    if override:
        return override
    if target.auth != "native" and (target.credential_id or target.auth == "managed"):
        document, _ = _read(path or credentials_path())
        entry = document.get("credentials", {}).get(target.credential_id)
        if not entry:
            raise CredentialError(
                "No saved credential is available for this target. Run "
                + setup_command(target.name)
                + " in your terminal."
            )
        if entry["binding"] != binding(target):
            raise CredentialError(
                "The saved credential belongs to different connection details. Run "
                + setup_command(target.name)
                + " --replace-password in your terminal."
            )
        return Credential(entry["password"], "managed_file")
    return Credential(None, "native")


def save_credential(target: Target, password: str, path: Path) -> str:
    if not password:
        raise CredentialError(
            "No password was entered. Existing credentials were preserved."
        )
    identifier = uuid4().hex
    try:
        with locked(path):
            document, original = _read(path)
            document.setdefault("credentials", tomlkit.table())[identifier] = {
                "password": password,
                "binding": binding(target),
            }
            atomic_write(path, tomlkit.dumps(document).encode("utf-8"), original)
        return identifier
    except OSError:
        raise CredentialError(
            f"Cannot save credentials:\n  {path}\nProvide SQL_AGENT_CLI_PASSWORD in the execution environment instead. No shell settings were changed."
        ) from None


def remove_credential(identifier: str, path: Path) -> None:
    with locked(path):
        document, original = _read(path)
        entries = document.get("credentials", {})
        if identifier in entries:
            del entries[identifier]
            atomic_write(path, tomlkit.dumps(document).encode("utf-8"), original)
