from __future__ import annotations

import argparse
import sys
from pathlib import Path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse messages may echo a pasted password-bearing URI or unknown flag.
        self.print_usage(sys.stderr)
        self.exit(
            2, f"Invalid arguments. Run {self.prog} --help for supported options.\n"
        )


def add_paths(parser):
    parser.add_argument(
        "--config",
        type=Path,
        help="Configuration file. Defaults to SQL_AGENT_CLI_CONFIG or ~/.sql-agent-cli/config.toml.",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        help="Separate credentials file. Defaults to SQL_AGENT_CLI_CREDENTIALS_FILE or credentials.toml beside the config file.",
    )


def add_auth_options(parser):
    parser.add_argument(
        "--auth",
        choices=("auto", "native", "managed"),
        help="Credential source. Environment credentials remain explicit overrides.",
    )
    parser.add_argument(
        "--auth-file", help="Selected MySQL or MariaDB native option file."
    )
    parser.add_argument("--service", help="Selected PostgreSQL connection service.")
    parser.add_argument("--unix-socket", help="MySQL or MariaDB socket path.")
    parser.add_argument("--ssl-ca", help="Trusted certificate authority file.")
