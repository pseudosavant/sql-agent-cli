from __future__ import annotations

import atexit
import importlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

_SKILL_HOME = tempfile.TemporaryDirectory(prefix="sql-agent-tests-")
atexit.register(_SKILL_HOME.cleanup)


def bootstrap_package() -> None:
    if "sql_agent" in sys.modules:
        return
    test_home = Path(_SKILL_HOME.name)
    for key in list(os.environ):
        if key.startswith("PG") or key in {
            "SQL_AGENT_CLI_CONFIG",
            "SQL_AGENT_CLI_CREDENTIALS_FILE",
            "SQL_AGENT_CLI_TARGET",
            "SQL_AGENT_CLI_PASSWORD",
        }:
            os.environ.pop(key)
    os.environ.update(
        HOME=str(test_home),
        USERPROFILE=str(test_home),
        APPDATA=str(test_home),
        SQL_AGENT_CLI_CONFIG=str(test_home / "config.toml"),
    )
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
    # Every CLI test uses a disposable skill root, even if source detection changes.
    skill = importlib.import_module("sql_agent.skill")
    skill.default_skills_dir = lambda: Path(_SKILL_HOME.name) / "skills"
