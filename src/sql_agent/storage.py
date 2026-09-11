from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import ConfigurationError


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ConfigurationError(
            f"Cannot read file:\n  {path}\nCheck access in this execution environment."
        ) from None


@contextmanager
def locked(path: Path):
    """Serialize CLI writers. Lock files contain no secrets and inherit permissions."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            path.with_name(f".{path.name}.lock"), os.O_CREAT | os.O_RDWR, 0o666
        )
    except OSError:
        raise ConfigurationError(
            f"Cannot write in the selected local directory:\n  {path.parent}\nCheck directory access or choose a writable --config and --credentials-file location, then rerun the command."
        ) from None
    acquired = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ConfigurationError(
                "Another configuration update is running. Wait for it to finish, then rerun the command."
            ) from None
        yield
    finally:
        if acquired:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_write(path: Path, content: bytes, expected: bytes | None) -> None:
    """Call under locked(path). Recheck external edits before atomic replacement."""
    if content == expected:
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        # Ordinary creation respects inherited ACLs and the process umask.
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink() or read_bytes(path) != expected:
            raise ConfigurationError(
                "The file changed during this update. No replacement was made. Rerun the command."
            )
        os.replace(temporary, path)
    except OSError:
        raise ConfigurationError(
            f"Cannot replace the local file:\n  {path}\nCheck directory access and file locks, then rerun the command. Existing content was preserved."
        ) from None
    finally:
        temporary.unlink(missing_ok=True)
