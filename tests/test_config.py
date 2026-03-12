from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import shutil
from uuid import uuid4

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent import config
from sql_agent.cli import main as cli_main
from sql_agent.models import AppConfig, Defaults, Target


def _test_temp_dir(name: str) -> Path:
    root = Path.cwd() / "test-output"
    root.mkdir(exist_ok=True)
    path = root / f"{name}-{uuid4().hex}"
    path.mkdir()
    return path


class ConfigTests(unittest.TestCase):
    def test_config_show_defaults_to_text_output(self) -> None:
        app_config = AppConfig(
            defaults=Defaults(target="dev", format="json"),
            targets={
                "dev": Target(
                    name="dev",
                    engine="mysql",
                    host="db.example.com",
                    port=3306,
                    database="app",
                    user="reader",
                    ssl_mode="required",
                )
            },
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        payload = config.build_show_payload(app_config)
        with patch("sql_agent.cli.load_config", return_value=app_config), patch(
            "sql_agent.cli.build_show_payload", return_value=payload
        ), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            exit_code = cli_main(["config", "show"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        text = stdout.getvalue()
        self.assertIn("Config", text)
        self.assertIn("Default Target: dev", text)
        self.assertIn("db.example.com:3306", text)

    def test_targets_support_json_format(self) -> None:
        app_config = AppConfig(
            defaults=Defaults(target="dev"),
            targets={"dev": Target(name="dev", engine="sqlite", path="C:/tmp/demo.db")},
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sql_agent.cli.load_config", return_value=app_config), patch("sys.stdout", stdout), patch(
            "sys.stderr", stderr
        ):
            exit_code = cli_main(["targets", "--format", "json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["default_target"], "dev")
        self.assertEqual(payload["targets"][0]["engine"], "sqlite")

    def test_resolve_target_uses_default_target_and_defaults(self) -> None:
        app_config = AppConfig(
            defaults=Defaults(target="dev", format="json", max_rows=25, connect_timeout_seconds=9),
            targets={
                "dev": Target(
                    name="dev",
                    engine="mysql",
                    host="db.example.com",
                    database="app",
                    user="reader",
                )
            },
        )
        args = SimpleNamespace(
            target=None,
            engine=None,
            database=None,
            user=None,
            host=None,
            port=None,
            path=None,
            ssl_mode=None,
            max_rows=None,
            connect_timeout_seconds=None,
            query_timeout_seconds=None,
            insecure=None,
        )

        target = config.resolve_target(app_config, args)

        self.assertEqual(target.name, "dev")
        self.assertEqual(target.engine, "mysql")
        self.assertEqual(target.port, 3306)
        self.assertEqual(target.ssl_mode, "required")
        self.assertEqual(target.max_rows, 25)
        self.assertEqual(target.connect_timeout_seconds, 9)

    def test_save_and_load_round_trip(self) -> None:
        temp_dir = _test_temp_dir("config-roundtrip")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        path = temp_dir / "config.toml"
        app_config = AppConfig(
            defaults=Defaults(target="dev", format="json", max_rows=50),
            targets={
                "dev": Target(
                    name="dev",
                    engine="sqlite",
                    path="C:/tmp/demo.db",
                )
            },
        )

        config.save_config(app_config, path)
        loaded = config.load_config(path)

        self.assertEqual(loaded.defaults.target, "dev")
        self.assertEqual(loaded.defaults.max_rows, 50)
        self.assertEqual(loaded.targets["dev"].engine, "sqlite")
        self.assertEqual(loaded.targets["dev"].path, "C:/tmp/demo.db")

    def test_create_mysql_native_auth_template_prefills_target(self) -> None:
        temp_dir = _test_temp_dir("mysql-auth-template")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        temp_path = temp_dir / "my.cnf"
        target = Target(
            name="dev",
            engine="mysql",
            host="db.example.com",
            port=3306,
            user="reader",
        )
        with patch("sql_agent.config.preferred_mysql_option_file_path", return_value=temp_path):
            created = config.create_native_auth_template("mysql", target)

        self.assertEqual(created, temp_path)
        contents = temp_path.read_text(encoding="utf-8")
        self.assertIn("host=db.example.com", contents)
        self.assertIn("user=reader", contents)
        self.assertIn("password=", contents)

    def test_create_postgres_template_refuses_to_overwrite(self) -> None:
        temp_dir = _test_temp_dir("postgres-auth-template")
        self.addCleanup(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        temp_path = temp_dir / "pgpass.conf"
        temp_path.write_text("existing", encoding="utf-8")
        with patch("sql_agent.config.preferred_postgres_auth_path", return_value=temp_path):
            with self.assertRaises(FileExistsError):
                config.create_native_auth_template("postgres", None)
