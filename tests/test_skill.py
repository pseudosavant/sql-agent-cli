from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent.cli import main
from sql_agent.skill import MANAGED_MARKER, SKILL_MD, install_skill, remove_skill
from sql_agent.validation import UsageError


class SkillTests(unittest.TestCase):
    def test_install_creates_updates_and_overwrites_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skills_root = Path(temp)
            first = install_skill(skills_root)
            skill_path = skills_root / "sql-agent-cli" / "SKILL.md"

            self.assertTrue(first["installed"])
            self.assertTrue(first["updated"])
            self.assertEqual(skill_path.read_text(encoding="utf-8"), SKILL_MD)

            skill_path.write_text(
                "---\nname: sql-agent-cli\n---\ncustom\n", encoding="utf-8"
            )
            second = install_skill(skills_root)
            self.assertTrue(second["updated"])
            self.assertEqual(skill_path.read_text(encoding="utf-8"), SKILL_MD)

            third = install_skill(skills_root)
            self.assertFalse(third["updated"])

    def test_skill_documents_safe_agent_workflow(self) -> None:
        self.assertIn("name: sql-agent-cli", SKILL_MD)
        self.assertIn("uvx sql-agent-cli", SKILL_MD)
        self.assertIn("Do not assume `sql-agent-cli` is installed locally", SKILL_MD)
        self.assertIn("configured default target", SKILL_MD)
        self.assertIn("exactly one read-only statement", SKILL_MD)
        self.assertIn("password hashes, tokens, secrets", SKILL_MD)
        self.assertIn("response guardrails, not database-work limits", SKILL_MD)
        self.assertIn("When `truncated` is `true`", SKILL_MD)
        self.assertIn("## Recover From Failures", SKILL_MD)
        self.assertIn("config check --format json", SKILL_MD)
        self.assertIn("Do not run this preflight before a normal first query", SKILL_MD)
        self.assertIn("$sql-agent-cli", SKILL_MD)
        self.assertIn("--password-stdin", SKILL_MD)
        self.assertIn("--insecure", SKILL_MD)
        self.assertIn("dedicated database roles", SKILL_MD)
        self.assertIn("requires encrypted transport", SKILL_MD)
        self.assertIn("native trust configuration", SKILL_MD)
        self.assertIn(MANAGED_MARKER, SKILL_MD)
        self.assertNotIn("Codex", SKILL_MD)
        self.assertNotIn("powershell", SKILL_MD.lower())

    def test_remove_only_removes_managed_skill_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            skills_root = Path(temp)
            install_skill(skills_root)
            removed = remove_skill(skills_root)
            self.assertTrue(removed["removed"])
            self.assertFalse((skills_root / "sql-agent-cli").exists())

            skill_dir = skills_root / "sql-agent-cli"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: sql-agent-cli\n---\ncustom\n",
                encoding="utf-8",
            )
            with self.assertRaises(UsageError):
                remove_skill(skills_root)
            self.assertTrue(remove_skill(skills_root, force=True)["removed"])

    def test_remove_missing_skill_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = remove_skill(Path(temp))

        self.assertFalse(result["removed"])
        self.assertEqual(result["reason"], "not_installed")

    def test_cli_install_and_remove_commands_output_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                install_code = main(["install-skill", "--skills-dir", temp])

            self.assertEqual(install_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            installed = json.loads(stdout.getvalue())
            self.assertEqual(installed["skill"], "sql-agent-cli")
            self.assertTrue((Path(temp) / "sql-agent-cli" / "SKILL.md").exists())

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                remove_code = main(["remove-skill", "--skills-dir", temp])

            self.assertEqual(remove_code, 0)
            self.assertEqual(stderr.getvalue(), "")
            removed = json.loads(stdout.getvalue())
            self.assertTrue(removed["removed"])

    def test_root_help_lists_skill_commands(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("install-skill", stdout.getvalue())
        self.assertIn("remove-skill", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
