#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "PyMySQL[rsa]>=1.1.0",
#   "psycopg[binary]>=3.2.0",
#   "sqlglot>=26.0.0",
# ]
# ///

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


def _bootstrap_src() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    package_dir = src / "sql_agent"
    spec = importlib.util.spec_from_file_location(
        "sql_agent",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to bootstrap package from {package_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sql_agent"] = module
    spec.loader.exec_module(module)


def main() -> int:
    _bootstrap_src()
    cli_main = importlib.import_module("sql_agent.cli").main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
