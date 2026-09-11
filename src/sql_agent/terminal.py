from __future__ import annotations

import sys
from contextlib import closing

from .errors import UsageError


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def masked_input(prompt: str = "Password: ") -> str:
    if not is_interactive():
        raise UsageError(
            "Masked input requires a terminal on stdin and stderr. Run setup in your terminal, or provide credentials in the execution environment."
        )
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import DummyHistory
    from prompt_toolkit.input import create_input
    from prompt_toolkit.output import create_output

    try:
        with closing(create_input(stdin=sys.stdin)) as terminal_input:
            session = PromptSession(
                input=terminal_input,
                output=create_output(stdout=sys.stderr),
                history=DummyHistory(),
                is_password=True,
                enable_suspend=False,
            )
            return session.prompt(prompt)
    except (EOFError, KeyboardInterrupt):
        raise UsageError("Setup cancelled. No password was saved.") from None
    except (OSError, ValueError, NotImplementedError):
        raise UsageError(
            "Masked input is unavailable. Run setup in an interactive terminal. No password was saved."
        ) from None


def ask(prompt: str) -> str:
    if not is_interactive():
        raise UsageError(
            "Setup needs more information. Run uvx sql-agent-cli setup in an interactive terminal."
        )
    print(f"\n{prompt}", file=sys.stderr, end="", flush=True)
    value = sys.stdin.readline()
    if not value:
        raise UsageError("Setup cancelled. No configuration was changed.")
    return value.strip()
