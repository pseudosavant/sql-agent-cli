"""Run against an installed distribution without the test source bootstrap."""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import tempfile
from contextlib import closing, redirect_stderr, redirect_stdout
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from sql_agent import __version__, skill
from sql_agent.cli import main


def smoke(expected_source: str) -> None:
    assert version("sql-agent-cli") == __version__
    assert skill.runtime_source() == expected_source, skill.runtime_source()
    with tempfile.TemporaryDirectory() as temporary:
        skills_root = Path(temporary) / "skills"
        with patch.object(skill, "default_skills_dir", return_value=skills_root):
            path = skill.skill_dir() / "SKILL.md"
            path.parent.mkdir(parents=True)
            older = skill.render_skill("0.0")
            path.write_bytes(older.encode("utf-8"))
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    main(["--version"])
                except SystemExit as exc:
                    assert exc.code == 0
            assert stdout.getvalue().strip() == f"sql-agent-cli {__version__}"
            if expected_source == "installed":
                assert "Updated skill" in stderr.getvalue()
                assert (
                    skill.inspect_skill(path.read_text(encoding="utf-8")).version
                    == __version__
                )
            else:
                assert stderr.getvalue() == ""
                assert path.read_bytes() == older.encode("utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                assert main(["skill", "install"]) == 0
            assert json.loads(stdout.getvalue())["installed"]
            assert path.read_bytes() == skill.render_skill().encode("utf-8")
            assert (
                skill.inspect_skill(path.read_text(encoding="utf-8")).integrity
                == "valid"
            )
            assert list(path.parent.iterdir()) == [path]
            database = Path(temporary) / "sample.db"
            configuration = Path(temporary) / "config.toml"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample (value INTEGER)")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                assert (
                    main(
                        [
                            "setup",
                            "--format",
                            "json",
                            str(database),
                            "--config",
                            str(configuration),
                            "--non-interactive",
                        ]
                    )
                    == 0
                ), stderr.getvalue()
            report = json.loads(stdout.getvalue())
            assert report["status"] == "ready"
            assert report["skill_ready"]
            assert report["verification"]["visible_relations"] == 1
            assert not configuration.with_name("credentials.toml").exists()
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                redirect_stdout(stdout),
                redirect_stderr(stderr),
                patch("sys.stdin", io.StringIO()),
            ):
                assert (
                    main(["--config", str(configuration), "PRAGMA table_info(sample)"])
                    == 0
                ), stderr.getvalue()
            assert json.loads(stdout.getvalue())["result"]["rows"][0][1] == "value"
    print(
        f"Installed distribution smoke passed: {expected_source}, version {__version__}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-source",
        choices=("installed", "local", "editable"),
        default="installed",
    )
    smoke(parser.parse_args().expected_source)
