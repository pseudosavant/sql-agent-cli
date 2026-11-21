"""Build and exercise wheel, source archive, uvx, and PEP 723 setup paths."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path


def run(command, cwd, env):
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, input="", check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"Packaging smoke failed ({result.returncode}).\n{result.stderr}"
        )
    return result.stdout


def smoke():
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="sql-agent-package-") as temporary:
        root = Path(temporary)
        # Every subprocess gets an isolated home and no database credential overrides.
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PG", "SQL_AGENT_CLI_"))
            and key != "CLAUDE_CONFIG_DIR"
        }
        env.update(HOME=str(root), USERPROFILE=str(root), APPDATA=str(root))
        run(["uv", "build", "--out-dir", str(root / "dist")], repository, env)
        wheel = next((root / "dist").glob("*.whl"))
        archive = next((root / "dist").glob("*.tar.gz"))
        for distribution, source in ((wheel, "installed"), (archive, "local")):
            environment = root / source
            run(["uv", "venv", "--python", sys.executable, str(environment)], root, env)
            python = environment / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            run(
                ["uv", "pip", "install", "--python", str(python), str(distribution)],
                root,
                env,
            )
            run(
                [
                    str(python),
                    str(repository / "tests/wheel_smoke.py"),
                    "--expected-source",
                    source,
                ],
                root,
                env,
            )
        database = root / "sample.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
        commands = {
            "uvx": [
                "uvx",
                "--python",
                sys.executable,
                "--from",
                str(wheel),
                "sql-agent-cli",
            ],
            "wrapper": [
                "uv",
                "run",
                "--python",
                sys.executable,
                str(repository / "sql_agent_cli.py"),
            ],
        }
        claude_skills = root / ".claude" / "skills"
        claude_skills.parent.mkdir()
        skill_file = Path("sql-agent-cli") / "SKILL.md"
        primary = root / ".agents" / "skills" / skill_file
        claude = claude_skills / skill_file
        assert "usage:" in run(commands["uvx"], root, env)
        assert not primary.exists() and not claude_skills.exists()
        for name, command in commands.items():
            configuration = root / name / "config.toml"
            output = run(
                [
                    *command,
                    "setup",
                    "--format",
                    "json",
                    str(database),
                    "--config",
                    str(configuration),
                    "--non-interactive",
                ],
                root,
                env,
            )
            assert json.loads(output)["status"] == "ready"
            assert claude.read_bytes() == primary.read_bytes()
            output = run(
                [*command, "--config", str(configuration), "PRAGMA table_info(sample)"],
                root,
                env,
            )
            assert json.loads(output)["result"]["rows"][0][1] == "value"
        run([*commands["uvx"], "skill", "remove"], root, env)
        assert "usage:" in run(commands["uvx"], root, env)
        assert not primary.exists() and not claude.exists()
    print("Packaging smoke passed: wheel, source archive, uvx, and PEP 723 wrapper.")


if __name__ == "__main__":
    smoke()
