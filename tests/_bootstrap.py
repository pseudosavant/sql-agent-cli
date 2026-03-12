from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def bootstrap_package() -> None:
    root = Path(__file__).resolve().parents[1]
    package_dir = root / "src" / "sql_agent"
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
