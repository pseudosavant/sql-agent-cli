from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent import auth, config, onboarding, skill
from sql_agent.cli import main
from sql_agent.errors import ConfigurationError, CredentialError, diagnostic
from sql_agent.models import AppConfig, Defaults, Target


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.configuration = self.root / "settings" / "config.toml"
        self.credentials = self.configuration.with_name("credentials.toml")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PG", "SQL_AGENT_CLI_"))
        }
        env.update(
            HOME=str(self.root),
            USERPROFILE=str(self.root),
            APPDATA=str(self.root),
            SQL_AGENT_CLI_CONFIG=str(self.configuration),
        )
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))
        self.stack.enter_context(patch("pathlib.Path.home", return_value=self.root))
        self.stack.enter_context(patch("sql_agent.cli.synchronize_skill"))
        self.stack.enter_context(
            patch.object(
                skill,
                "default_skills_dir",
                return_value=self.root / ".agents" / "skills",
            )
        )
        self.database = self.root / "demo.db"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
            connection.commit()

    def call(self, *args, input_text=""):
        out, err = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            patch("sys.stdin", io.StringIO(input_text)),
        ):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def network(self, **changes):
        target = Target(
            name="reporting",
            engine="postgres",
            host="db.example.test",
            port=5432,
            user="reader",
            database="app",
            ssl_mode="verify-full",
        )
        for key, value in changes.items():
            setattr(target, key, value)
        return target

    def verify(self, target):
        return {
            "status": "passed",
            "connection": "passed",
            "authentication": "passed",
            "read_only": "passed",
            "catalog": "passed",
        }

    def test_setup_defaults_to_text_and_accepts_explicit_text(self):
        for flags in ((), ("--format", "text")):
            with self.subTest(flags=flags):
                code, output, errors = self.call("setup", str(self.database), *flags)
                self.assertEqual(code, 0, errors)
                self.assertTrue(output.startswith("Setup complete."))
                self.assertIn("Database verified: true", output)
                self.assertIn("Skill ready: true", output)
                self.assertNotIn("Verifying the connection", output)
                self.assertIn("Verifying the connection", errors)

    def test_noninteractive_setup_failure_defaults_to_text(self):
        code, output, errors = self.call("setup", "--non-interactive")
        self.assertEqual(code, 2)
        self.assertTrue(output.startswith("Setup incomplete."))
        self.assertIn("Configuration saved: false", output)
        self.assertIn("Missing --engine", errors)

    def test_clean_sqlite_setup_and_first_skill_query(self):
        before = self.database.read_bytes()
        code, output, errors = self.call(
            "setup", "--format", "json", str(self.database), "--non-interactive"
        )
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["verification"]["visible_relations"], 1)
        self.assertEqual(report["verification"]["table_access"], "not_checked")
        self.assertEqual(config.load_config().defaults.target, "default")
        self.assertEqual(
            config.load_config().targets["default"].path, str(self.database.resolve())
        )
        self.assertFalse(self.credentials.exists())
        self.assertTrue((self.root / ".agents/skills/sql-agent-cli/SKILL.md").is_file())
        self.assertFalse((self.root / ".claude").exists())
        self.assertEqual(self.database.read_bytes(), before)
        code, output, errors = self.call(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["result"]["rows"], [["sample"]])

    def test_repeat_setup_preserves_comments_unknown_fields_and_default(self):
        self.call(
            "setup", "--format", "json", str(self.database), "--target", "local.one"
        )
        with self.configuration.open("a") as handle:
            handle.write('\n# Keep this preference\n[extension]\ncolor = "blue"\n')
        before = self.configuration.read_bytes()
        self.assertEqual(self.call("setup", "--format", "json")[0], 0)
        self.assertEqual(self.configuration.read_bytes(), before)
        self.assertEqual(
            self.call(
                "setup", "--format", "json", str(self.database), "--target", "local.two"
            )[0],
            0,
        )
        saved = config.load_config()
        self.assertEqual(saved.defaults.target, "local.one")
        self.assertEqual(set(saved.targets), {"local.one", "local.two"})
        self.assertIn(b"# Keep this preference", self.configuration.read_bytes())

    def test_invalid_sqlite_file_and_missing_file_are_not_saved_or_created(self):
        invalid = self.root / "invalid.db"
        invalid.write_text("not a database")
        for path in (invalid, self.root / "missing.db"):
            code, output, _ = self.call("setup", "--format", "json", str(path))
            self.assertNotEqual(code, 0)
            self.assertFalse(json.loads(output)["configuration_saved"])
            self.assertFalse(self.configuration.exists())
            self.assertFalse(skill.skill_dir().exists())
        self.assertFalse((self.root / "missing.db").exists())

    def test_interactive_password_saved_separately_only_after_verification(self):
        secret = " dummy password with spaces "

        def verify(target):
            self.assertEqual(target.password, secret)
            self.assertFalse(self.credentials.exists())
            return self.verify(target)

        with (
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(onboarding.terminal, "masked_input", return_value=secret),
            patch.object(onboarding, "verify_target", side_effect=verify),
        ):
            code, output, errors = self.call(
                "setup", "--format", "json", "postgres://reader@db.example.test/app"
            )
        self.assertEqual(code, 0, errors)
        self.assertNotIn(secret, output + errors + self.configuration.read_text())
        target = config.resolve_target(config.load_config(), SimpleNamespace())
        self.assertEqual(auth.resolve_credential(target).password, secret)
        self.assertEqual(auth.resolve_credential(target).source, "managed_file")
        with (
            patch.object(onboarding, "verify_target", side_effect=self.verify),
            patch.object(onboarding.terminal, "masked_input") as prompt,
        ):
            before = self.credentials.read_bytes()
            self.assertEqual(
                self.call("setup", "--format", "json", "--non-interactive")[0], 0
            )
            self.assertEqual(self.credentials.read_bytes(), before)
            prompt.assert_not_called()

    def test_failed_password_replacement_preserves_config_credentials_and_skill(self):
        target = self.network()
        target.credential_id = auth.save_credential(
            target, "old-dummy", self.credentials
        )
        target.auth = "managed"
        config.save_config(
            AppConfig(Defaults(target=target.name), {target.name: target})
        )
        originals = self.configuration.read_bytes(), self.credentials.read_bytes()
        failure = {
            "status": "failed",
            "error": diagnostic(
                RuntimeError("password authentication failed secret-from-driver"),
                target.name,
            ),
        }
        with (
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(onboarding.terminal, "masked_input", return_value="new-dummy"),
            patch.object(onboarding, "verify_target", return_value=failure),
        ):
            code, out, err = self.call(
                "setup", "--format", "json", "--replace-password"
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"]["code"], "authentication_failed")
        for secret in ("old-dummy", "new-dummy", "secret-from-driver"):
            self.assertNotIn(secret, out + err)
        self.assertEqual(
            (self.configuration.read_bytes(), self.credentials.read_bytes()), originals
        )
        self.assertFalse(skill.skill_dir().exists())

    def test_successful_replacement_preserves_other_credential_and_removes_old(self):
        target = self.network()
        old = auth.save_credential(target, "old-dummy", self.credentials)
        other = auth.save_credential(
            self.network(user="other"), "unrelated-dummy", self.credentials
        )
        target.credential_id, target.auth = old, "managed"
        config.save_config(
            AppConfig(Defaults(target=target.name), {target.name: target})
        )
        with (
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(
                onboarding.terminal, "masked_input", return_value="replacement-dummy"
            ),
            patch.object(onboarding, "verify_target", side_effect=self.verify),
        ):
            code, _, errors = self.call(
                "setup", "--format", "json", "--replace-password"
            )
        self.assertEqual(code, 0, errors)
        document, _ = auth._read(self.credentials)
        self.assertNotIn(old, document["credentials"])
        self.assertIn(other, document["credentials"])
        self.assertEqual(len(document["credentials"]), 2)

    def test_config_commit_failure_removes_staged_password(self):
        with (
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(
                onboarding.terminal, "masked_input", return_value="staged-dummy"
            ),
            patch.object(onboarding, "verify_target", side_effect=self.verify),
            patch.object(
                onboarding,
                "save_config",
                side_effect=ConfigurationError("Concurrent edit. Rerun setup."),
            ),
        ):
            code, _, _ = self.call(
                "setup", "--format", "json", "postgres://reader@db.example.test/app"
            )
        self.assertNotEqual(code, 0)
        self.assertNotIn("staged-dummy", self.credentials.read_text())
        self.assertFalse(self.configuration.exists())

    def test_environment_override_is_never_saved_and_does_not_require_terminal(self):
        with (
            patch.dict(os.environ, {"SQL_AGENT_CLI_PASSWORD": "environment-dummy"}),
            patch.object(onboarding, "verify_target", side_effect=self.verify),
        ):
            code, output, errors = self.call(
                "setup",
                "--format",
                "json",
                "mysql://reader@db.example.test/app",
                "--non-interactive",
                input_text="ignored-stdin-dummy",
            )
        self.assertEqual(code, 0, errors)
        self.assertEqual(
            json.loads(output)["credential_source"], "SQL_AGENT_CLI_PASSWORD"
        )
        self.assertFalse(self.credentials.exists())
        self.assertNotIn(
            "environment-dummy", self.configuration.read_text() + output + errors
        )

    def test_noninteractive_missing_information_never_reads_stdin_or_prompts(self):
        with (
            patch.object(
                onboarding.terminal, "ask", side_effect=AssertionError("prompt")
            ),
            patch.object(
                onboarding.terminal,
                "masked_input",
                side_effect=AssertionError("password prompt"),
            ),
        ):
            for args in (
                ("--non-interactive",),
                ("postgres://reader@db.example.test/app", "--non-interactive"),
                ("--replace-password", "--non-interactive"),
            ):
                code, out, _ = self.call(
                    "setup", "--format", "json", *args, input_text="must-not-be-used"
                )
                self.assertEqual(code, 2)
                self.assertFalse(json.loads(out)["configuration_saved"])
        self.assertFalse(self.configuration.exists())

    def test_uri_parsing_and_secret_rejection(self):
        target = onboarding.parse_resource(
            "postgresql://reader@db.example.test:5433/my%20db?sslmode=verify-full"
        )
        self.assertEqual(
            (target.engine, target.database, target.port), ("postgres", "my db", 5433)
        )
        for url in (
            "postgres://reader:argument-dummy@host/app",
            "mysql://host/app?password=argument-dummy",
        ):
            code, out, err = self.call("setup", "--format", "json", url)
            self.assertEqual(code, 2)
            self.assertNotIn("argument-dummy", out + err)
        self.assertFalse(self.configuration.exists())

    def test_read_only_checks_do_not_synchronize_or_change_any_files(self):
        self.call("setup", "--format", "json", str(self.database))
        paths = [self.configuration, self.database, skill.skill_dir() / "SKILL.md"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        with (
            patch("sql_agent.cli.synchronize_skill") as sync,
            patch(
                "sql_agent.cli.verify_target",
                side_effect=AssertionError("offline connected"),
            ),
        ):
            code, out, err = self.call(
                "config", "check", "--offline", "--format", "json"
            )
            self.assertEqual(code, 0, err)
            self.assertIsNone(json.loads(out)["database_ready"])
            sync.assert_not_called()
        self.assertEqual(self.call("config", "check", "--format", "json")[0], 0)
        self.assertEqual(
            {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}, before
        )
        self.assertEqual(self.call("config")[0], 0)

    def test_modified_skill_is_preserved_and_partial_success_is_explicit(self):
        path = skill.skill_dir() / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(skill.render_skill() + "\nLocal edit\n")
        before = path.read_bytes()
        code, out, _ = self.call("setup", "--format", "json", str(self.database))
        self.assertNotEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["database_ready"])
        self.assertTrue(report["configuration_saved"])
        self.assertFalse(report["skill_ready"])
        self.assertEqual(path.read_bytes(), before)

    def test_precedence_binding_and_corrupt_file_bypass(self):
        target = self.network()
        target.credential_id = auth.save_credential(
            target, "saved-dummy", self.credentials
        )
        self.assertEqual(auth.resolve_credential(target).password, "saved-dummy")
        with patch.dict(
            os.environ,
            {"PGPASSWORD": "pg-dummy", "SQL_AGENT_CLI_PASSWORD": "generic-dummy"},
        ):
            self.assertEqual(auth.resolve_credential(target).password, "generic-dummy")
            target.password = "input-dummy"
            self.assertEqual(auth.resolve_credential(target).password, "input-dummy")
            target.password = None
            self.credentials.write_text("invalid TOML dummy-secret")
            self.assertEqual(auth.resolve_credential(target).password, "generic-dummy")
        with self.assertRaises(CredentialError):
            auth.resolve_credential(target)
        self.credentials.unlink()
        target.credential_id = auth.save_credential(
            target, "saved-dummy", self.credentials
        )
        target.host = "different.example.test"
        with self.assertRaises(CredentialError):
            auth.resolve_credential(target)
        target.auth = "native"
        self.assertIsNone(auth.resolve_credential(target).password)

    def test_native_details_take_precedence_over_builtin_port_defaults(self):
        with patch.dict(
            os.environ,
            {
                "PGPORT": "5439",
                "PGHOST": "db.example.test",
                "PGDATABASE": "app",
                "PGUSER": "reader",
            },
        ):
            target = config.resolve_target(
                AppConfig(Defaults(), {}), SimpleNamespace(engine="postgres")
            )
            self.assertEqual(target.port, 5439)
        option = self.root / "client.cnf"
        option.write_text(
            '[client]\nhost="db.example.test"\nport=3319\ndatabase=app\nuser=reader\npassword=dummy-native\n'
        )
        target = config.resolve_target(
            AppConfig(Defaults(), {}),
            SimpleNamespace(engine="mysql", auth_file=str(option)),
        )
        self.assertEqual(
            (target.port, target.user, target.host), (3319, "reader", "db.example.test")
        )
        self.assertNotIn("dummy-native", str(target.public_dict()))

    def test_concurrent_configuration_edit_is_preserved(self):
        self.call("setup", "--format", "json", str(self.database))
        saved = config.load_config()
        self.configuration.write_text(
            self.configuration.read_text() + "\n# Other process\n"
        )
        with self.assertRaises(ConfigurationError):
            config.save_config(saved)
        self.assertIn("# Other process", self.configuration.read_text())

    def test_check_rejects_ambiguous_password_input(self):
        for flags in (
            ("--all", "--password-stdin"),
            ("--offline", "--prompt-password"),
        ):
            code, _, _ = self.call("config", "check", *flags)
            self.assertEqual(code, 2)

    def test_real_password_reader_masks_echo_and_uses_stderr(self):
        from prompt_toolkit.data_structures import Size
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output.vt100 import Vt100_Output

        from sql_agent import terminal

        screen = io.StringIO()
        output = Vt100_Output(screen, lambda: Size(rows=24, columns=80), term="xterm")
        with (
            create_pipe_input() as pipe,
            patch.object(terminal, "is_interactive", return_value=True),
            patch("prompt_toolkit.input.create_input", return_value=pipe),
            patch(
                "prompt_toolkit.output.create_output", return_value=output
            ) as create_output,
            patch("sys.stderr", screen),
        ):
            pipe.send_text("dummy-mask-value\r")
            result = terminal.masked_input()
        self.assertEqual(result, "dummy-mask-value")
        self.assertNotIn("dummy-mask-value", screen.getvalue())
        self.assertIn("****************", screen.getvalue())
        create_output.assert_called_once_with(stdout=screen)

    def test_replacement_is_not_allowed_to_save_an_environment_override(self):
        with (
            patch.dict(os.environ, {"SQL_AGENT_CLI_PASSWORD": "external-dummy"}),
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(onboarding.terminal, "masked_input") as prompt,
        ):
            code, _, _ = self.call(
                "setup",
                "--format",
                "json",
                "postgres://reader@db.example.test/app",
                "--replace-password",
            )
        self.assertEqual(code, 2)
        prompt.assert_not_called()
        self.assertFalse(self.credentials.exists())

    def test_interactive_setup_repairs_rejected_native_credential_in_one_flow(self):
        failure = {
            "status": "failed",
            "error": diagnostic(RuntimeError("password authentication failed")),
        }
        with (
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(onboarding, "_native_available", return_value=True),
            patch.object(
                onboarding.terminal, "masked_input", return_value="replacement-dummy"
            ) as prompt,
            patch.object(
                onboarding, "verify_target", side_effect=[failure, self.verify(None)]
            ),
        ):
            code, out, err = self.call(
                "setup", "--format", "json", "postgres://reader@db.example.test/app"
            )
        self.assertEqual(code, 0, err)
        prompt.assert_called_once_with()
        self.assertEqual(json.loads(out)["credential_source"], "input")
        self.assertIn("replacement-dummy", self.credentials.read_text())

    def test_environment_failure_reports_environment_recovery_without_prompting(self):
        failure = {
            "status": "failed",
            "error": diagnostic(RuntimeError("password authentication failed")),
        }
        with (
            patch.dict(os.environ, {"SQL_AGENT_CLI_PASSWORD": "external-dummy"}),
            patch.object(onboarding.terminal, "is_interactive", return_value=True),
            patch.object(onboarding.terminal, "masked_input") as prompt,
            patch.object(onboarding, "verify_target", return_value=failure),
        ):
            code, out, err = self.call(
                "setup", "--format", "json", "postgres://reader@db.example.test/app"
            )
        self.assertEqual(code, 1)
        prompt.assert_not_called()
        self.assertIn("Renew or replace SQL_AGENT_CLI_PASSWORD", err)
        self.assertEqual(
            json.loads(out)["error"]["next_steps"],
            ["uvx sql-agent-cli setup --target default"],
        )
        self.assertFalse(self.configuration.exists())

    def test_failed_credential_file_replace_preserves_existing_secrets(self):
        target = self.network()
        auth.save_credential(target, "old-dummy", self.credentials)
        before = self.credentials.read_bytes()
        with (
            patch(
                "sql_agent.storage.os.replace",
                side_effect=PermissionError("sensitive-driver-text"),
            ),
            self.assertRaises(ConfigurationError) as raised,
        ):
            auth.save_credential(target, "new-dummy", self.credentials)
        self.assertIn("Check directory access and file locks", str(raised.exception))
        self.assertNotIn("sensitive-driver-text", str(raised.exception))
        self.assertEqual(self.credentials.read_bytes(), before)
        self.assertEqual(list(self.credentials.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
