from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from importlib import metadata
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tests._bootstrap import bootstrap_package

bootstrap_package()

from sql_agent import __version__, skill
from sql_agent.cli import main
from sql_agent.models import AppConfig, Defaults
from sql_agent.validation import UsageError


def resign(text: str) -> str:
    """Independent fixture hashing, without the implementation's digest."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    empty = re.sub(r"(?m)^(  managed-content-sha256: ).*$", r'\1""', text, count=1)
    digest = hashlib.sha256(empty.encode("utf-8")).hexdigest()
    return empty.replace(
        'managed-content-sha256: ""', f'managed-content-sha256: "sha256:{digest}"', 1
    )


class SkillSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skills_root = self.root / "skills"
        self.path = self.skills_root / skill.SKILL_NAME / "SKILL.md"
        for patcher in (
            patch.object(skill, "default_skills_dir", return_value=self.skills_root),
            patch.object(skill, "runtime_source", return_value="installed"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_skill(
        self, content: str | None = None, *, version: str = "0.11.0"
    ) -> str:
        if content is None:
            content = resign(
                skill.render_skill(version).replace(
                    "# SQL Agent CLI", "# Older SQL instructions"
                )
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(content.encode("utf-8"))
        return content

    def sync(self) -> str:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            skill.synchronize_skill()
        return stderr.getvalue()

    def cli(self, argv: list[str]) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            patch("sys.stdin", io.StringIO()),
        ):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_install_metadata_version_hash_and_file_encoding(self) -> None:
        result = skill.install_skill()
        data = self.path.read_bytes()
        text = data.decode("utf-8")
        front = yaml.safe_load(text.split("---", 2)[1])
        self.assertTrue(result["installed"])
        self.assertTrue(result["updated"])
        self.assertEqual(set(result), {"installed", "updated", "skill", "path"})
        self.assertEqual(front["name"], skill.SKILL_NAME)
        self.assertEqual(front["metadata"]["managed-by"], "sql-agent-cli")
        self.assertEqual(front["metadata"]["managed-version"], __version__)
        self.assertIn(f'managed-version: "{__version__}"', text)
        self.assertRegex(front["metadata"][skill.HASH_FIELD], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(resign(text), text)
        self.assertNotIn("version", front)
        self.assertNotIn(skill.MANAGED_MARKER, text)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_normalized_line_endings_verify(self) -> None:
        text = skill.render_skill()
        for ending in ("\n", "\r\n", "\r"):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(
                    skill.inspect_skill(text.replace("\n", ending)).integrity, "valid"
                )

    def test_body_front_matter_version_and_metadata_are_hashed(self) -> None:
        text = skill.render_skill()
        for changed in (
            text + "Additional guidance\n",
            text.replace("name: sql-agent-cli", "name: customized"),
            text.replace("description: Run safe", "description: Run custom"),
            text.replace("metadata:\n", "metadata:\n  author: Someone\n"),
            text.replace(f'"{__version__}"', '"0.11.0"'),
        ):
            self.assertEqual(skill.inspect_skill(changed).integrity, "altered")

    def test_hash_preserves_yaml_formatting_and_replaces_only_its_value(self) -> None:
        text = skill.render_skill("0.11.0")
        text = text.replace(
            "metadata:\n", "metadata:\n  author: 'Renée' # keep formatting\n"
        )
        text += 'managed-content-sha256: "a body example"\n'
        text = resign(text)
        self.assertEqual(skill.inspect_skill(text).integrity, "valid")
        self.assertEqual(
            skill.inspect_skill(
                text.replace("# keep formatting", "# changed")
            ).integrity,
            "altered",
        )
        with patch.object(
            skill,
            "_SKILL_TEMPLATE",
            skill._SKILL_TEMPLATE.replace(
                "metadata:\n", "metadata:\n  author: Example\n"
            ),
        ):
            rendered = skill.render_skill()
        self.assertIn("  author: Example", rendered)
        self.assertEqual(skill.inspect_skill(rendered).integrity, "valid")

    def test_absent_skill_or_file_is_never_automatically_installed(self) -> None:
        self.assertEqual(self.sync(), "")
        self.assertFalse(self.skills_root.exists())
        self.path.parent.mkdir(parents=True)
        self.assertEqual(self.sync(), "")
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_unmanaged_and_conflicting_legacy_skills_are_preserved(self) -> None:
        for text in (
            "---\nname: custom\n---\nCustom skill\n",
            f"---\nmetadata:\n  managed-by: another-tool\n---\n{skill.MANAGED_MARKER}\n",
            f"---\nmetadata:\n  managed-by: null\n---\n{skill.MANAGED_MARKER}\n",
        ):
            self.write_skill(text)
            self.assertEqual(self.sync(), "")
            self.assertEqual(self.path.read_text(encoding="utf-8"), text)
            for force in (False, True):
                with self.assertRaisesRegex(UsageError, "unmanaged"):
                    skill.install_skill(force=force)

    def test_pristine_older_skill_updates_from_its_own_hash(self) -> None:
        old = self.write_skill()
        self.assertNotEqual(old, skill.render_skill("0.11.0"))
        notice = self.sync()
        self.assertIn(f"0.11.0 -> {__version__}", notice)
        self.assertIn(str(self.path), notice)
        self.assertEqual(len(notice.splitlines()), 1)
        self.assertEqual(self.path.read_bytes(), skill.render_skill().encode("utf-8"))

    def test_altered_missing_malformed_and_mismatched_hashes_are_preserved(
        self,
    ) -> None:
        original = skill.render_skill("0.11.0")
        variants = {
            "altered": original + "Edited\n",
            "missing": re.sub(r"(?m)^  managed-content-sha256:.*\n", "", original),
            "malformed": re.sub(r"sha256:[0-9a-f]{64}", "SHA256:INVALID", original),
            "mismatch": re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, original),
        }
        for kind, text in variants.items():
            with self.subTest(kind=kind):
                self.write_skill(text)
                self.assertEqual(
                    skill.inspect_skill(text).integrity,
                    "altered" if kind == "mismatch" else kind,
                )
                self.assertIn(skill.FORCE_INSTALL_COMMAND, self.sync())
                self.assertEqual(self.path.read_text(encoding="utf-8"), text)
                with self.assertRaisesRegex(
                    UsageError, re.escape(skill.FORCE_INSTALL_COMMAND)
                ):
                    skill.install_skill()
                self.assertTrue(skill.install_skill(force=True)["updated"])
                self.assertEqual(
                    self.path.read_text(encoding="utf-8"), skill.render_skill()
                )

    def test_equal_and_newer_versions_are_quiet_and_never_rewritten(self) -> None:
        for version in (__version__, "1.0", "1!0.1"):
            for altered in (False, True):
                with self.subTest(version=version, altered=altered):
                    text = skill.render_skill(version) + ("Edited\n" if altered else "")
                    self.write_skill(text)
                    before = self.path.stat().st_mtime_ns
                    with patch.object(skill.os, "replace") as replace:
                        self.assertEqual(self.sync(), "")
                        replace.assert_not_called()
                    self.assertEqual(self.path.stat().st_mtime_ns, before)
                    if version != __version__:
                        self.assertFalse(skill.install_skill(force=True)["updated"])
                    self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_pep440_ordering_and_exact_runtime_version(self) -> None:
        for current, old in (
            ("0.12.0", "0.9"),
            ("1.0", "1.0rc1"),
            ("1.0.post1", "1.0"),
            ("1.0rc1", "1.0.dev1"),
        ):
            with (
                self.subTest(current=current, old=old),
                patch.object(skill, "__version__", current),
            ):
                self.write_skill(version=old)
                self.assertIn(f"{old} -> {current}", self.sync())
                self.assertEqual(
                    skill.inspect_skill(self.path.read_text(encoding="utf-8")).version,
                    current,
                )
        with patch.object(skill, "__version__", "v1.0.0"):
            self.write_skill(version="1.0")
            self.assertEqual(self.sync(), "")
            self.assertIn('managed-version: "v1.0.0"', skill.render_skill())

    def test_legacy_and_invalid_or_missing_version_recovery(self) -> None:
        for text in (
            f"---\nname: sql-agent-cli\n---\n{skill.MANAGED_MARKER}\nOld body\n",
            "---\nmetadata:\n  managed-by: sql-agent-cli\n---\nEdited body\n",
            "---\nmetadata:\n  managed-by: sql-agent-cli\n  managed-version: broken\n---\nEdited body\n",
            "---\nmetadata:\n  managed-by: sql-agent-cli\n  managed-version: 12\n---\nEdited body\n",
        ):
            self.write_skill(text)
            state = skill.inspect_skill(text)
            self.assertTrue(state.recovery)
            if skill.MANAGED_MARKER in text:
                self.assertEqual(state.version, "0")
                self.assertEqual(state.integrity, "legacy")
            self.assertIn(f"-> {__version__}", self.sync())
            self.assertEqual(
                self.path.read_text(encoding="utf-8"), skill.render_skill()
            )

    def test_invalid_running_version_skips_before_reading_skill(self) -> None:
        with (
            patch.object(skill, "__version__", "invalid"),
            patch.object(skill, "_read_skill") as read,
        ):
            self.assertEqual(self.sync(), "")
            read.assert_not_called()

    def test_local_editable_and_unknown_builds_skip_but_explicit_install_works(
        self,
    ) -> None:
        for source in ("local", "editable", "unknown"):
            with (
                self.subTest(source=source),
                patch.object(skill, "runtime_source", return_value=source),
            ):
                old = self.write_skill()
                with patch.object(skill, "_read_skill") as read:
                    self.assertEqual(self.sync(), "")
                    read.assert_not_called()
                self.assertEqual(self.path.read_text(encoding="utf-8"), old)
                self.assertTrue(skill.install_skill()["updated"])

    def test_custom_location_requires_explicit_commands(self) -> None:
        custom = self.root / "custom"
        custom_path = custom / skill.SKILL_NAME / "SKILL.md"
        self.assertTrue(skill.install_skill(custom)["updated"])
        custom_path.write_bytes(skill.render_skill("0.11.0").encode("utf-8"))
        self.assertEqual(self.sync(), "")
        self.assertFalse(self.skills_root.exists())
        self.assertEqual(
            skill.inspect_skill(custom_path.read_text(encoding="utf-8")).version,
            "0.11.0",
        )
        status = skill.skill_status(custom)
        self.assertFalse(status["automatic_sync_eligible"])
        self.assertEqual(status["automatic_sync_reason"], "custom_directory")
        self.assertTrue(skill.install_skill(custom)["updated"])
        self.assertTrue(skill.remove_skill(custom)["removed"])

    def test_explicit_install_is_idempotent_and_force_restores_equal_version_edits(
        self,
    ) -> None:
        skill.install_skill()
        with patch.object(skill.os, "replace") as replace:
            self.assertFalse(skill.install_skill()["updated"])
            replace.assert_not_called()
        self.write_skill(skill.render_skill() + "Edit\n")
        with self.assertRaises(UsageError):
            skill.install_skill()
        self.assertTrue(skill.install_skill(force=True)["updated"])

    def test_skill_management_commands_and_aliases_never_synchronize(self) -> None:
        for argv in (
            ["skill"],
            ["skill", "install"],
            ["skill", "status"],
            ["skill", "remove"],
            ["install-skill"],
            ["remove-skill"],
            ["skill", "install", "--force"],
            ["skill", "remove", "--force"],
            ["skill", "--help"],
            ["install-skill", "--help"],
            ["remove-skill", "--help"],
        ):
            with (
                self.subTest(argv=argv),
                patch("sql_agent.cli.synchronize_skill") as sync,
            ):
                self.assertEqual(self.cli(argv)[0], 0)
                sync.assert_not_called()

    def test_all_normal_invocations_synchronize_before_output(self) -> None:
        for argv in (
            [],
            ["--help"],
            ["--version"],
            ["--about"],
            ["targets", "--format", "json"],
            ["config", "show", "--format", "json"],
        ):
            with (
                self.subTest(argv=argv),
                patch(
                    "sql_agent.cli.load_config", return_value=AppConfig(Defaults(), {})
                ),
            ):
                self.write_skill()
                code, stdout, stderr = self.cli(argv)
                self.assertEqual(code, 0)
                self.assertTrue(stdout)
                self.assertIn(f"-> {__version__}", stderr)

    def test_rendering_sync_keeps_json_stdout_valid(self) -> None:
        database = self.root / "query.db"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
        self.write_skill()
        code, stdout, stderr = self.cli(
            ["--engine", "sqlite", "--path", str(database), "SELECT 42 AS value"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["result"]["rows"], [[42]])
        self.assertIn("Updated skill", stderr)

    def test_sync_failure_keeps_primary_exit_status_and_json(self) -> None:
        self.write_skill()
        for failure in (
            PermissionError("read denied"),
            ValueError("bad metadata"),
            RuntimeError("unexpected"),
        ):
            with (
                self.subTest(failure=failure),
                patch.object(skill, "_read_skill", side_effect=failure),
                patch(
                    "sql_agent.cli.load_config", return_value=AppConfig(Defaults(), {})
                ),
            ):
                code, stdout, stderr = self.cli(["targets", "--format", "json"])
                self.assertEqual(code, 0)
                self.assertIn("targets", json.loads(stdout))
                self.assertIn("Skill synchronization skipped", stderr)

    def test_status_plain_json_and_integrity_are_read_only(self) -> None:
        self.write_skill()
        before = self.path.read_bytes()
        for output in ("text", "json"):
            code, stdout, stderr = self.cli(["skill", "status", "--format", output])
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            if output == "json":
                status = json.loads(stdout)
                for field in (
                    "installed",
                    "managed",
                    "standard_location",
                    "automatic_sync_eligible",
                ):
                    self.assertTrue(status[field])
                self.assertEqual(status["path"], str(self.path))
                self.assertEqual(status["cli_version"], __version__)
                self.assertEqual(status["installed_version"], "0.11.0")
                self.assertEqual(status["version_comparison"], "older")
                self.assertEqual(status["integrity"], "valid")
                self.assertFalse(status["local_development"])
            else:
                self.assertIn(str(self.path), stdout)
                self.assertIn("automatic sync eligible: true", stdout)
        self.assertEqual(self.path.read_bytes(), before)

    def test_status_reports_versions_alterations_and_development(self) -> None:
        for version, comparison in ((__version__, "equal"), ("1.0", "newer")):
            self.write_skill(version=version)
            status = skill.skill_status()
            self.assertEqual(status["version_comparison"], comparison)
            self.assertFalse(status["automatic_sync_eligible"])
        self.write_skill(skill.render_skill("0.11.0") + "Edit\n")
        status = skill.skill_status()
        self.assertEqual(status["integrity"], "altered")
        self.assertEqual(status["force_install_command"], skill.FORCE_INSTALL_COMMAND)
        with patch.object(skill, "runtime_source", return_value="editable"):
            status = skill.skill_status()
            self.assertTrue(status["local_development"])
            self.assertEqual(status["automatic_sync_reason"], "local_development")
            self.assertFalse(status["automatic_sync_eligible"])

    def test_status_absent_and_unmanaged(self) -> None:
        status = skill.skill_status()
        self.assertFalse(status["installed"])
        self.assertFalse(status["automatic_sync_eligible"])
        self.assertFalse(self.skills_root.exists())
        self.write_skill("custom\n")
        status = skill.skill_status()
        self.assertFalse(status["managed"])
        self.assertIsNone(status["installed_version"])
        self.assertEqual(status["integrity"], "not_applicable")

    def test_cli_force_json_contract_and_help(self) -> None:
        for install, remove in (
            (["install-skill"], ["remove-skill"]),
            (["skill", "install"], ["skill", "remove"]),
        ):
            code, stdout, stderr = self.cli(
                [*install, "--skills-dir", str(self.skills_root)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(json.loads(stdout)["installed"])
            self.write_skill(skill.render_skill() + "Edited\n")
            code, stdout, stderr = self.cli(install)
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn(skill.FORCE_INSTALL_COMMAND, stderr)
            self.assertEqual(self.cli([*install, "--force"])[0], 0)
            self.assertTrue(json.loads(self.cli(remove)[1])["removed"])
        self.assertIn("--force", self.cli(["skill", "install", "--help"])[1])
        self.assertIn("skill status", self.cli([])[1])

    def test_atomic_replace_observes_old_and_complete_new_files(self) -> None:
        old = self.write_skill()
        replace = skill.os.replace

        def observe(source: Path, destination: Path) -> None:
            self.assertEqual(source.parent, destination.parent)
            self.assertEqual(destination.read_text(encoding="utf-8"), old)
            self.assertEqual(source.read_bytes(), skill.render_skill().encode("utf-8"))
            replace(source, destination)
            self.assertEqual(
                destination.read_text(encoding="utf-8"), skill.render_skill()
            )

        with patch.object(skill.os, "replace", side_effect=observe) as replacement:
            self.assertIn("Updated skill", self.sync())
            replacement.assert_called_once()
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_write_and_replace_preserve_old_file_and_clean_temporary_files(
        self,
    ) -> None:
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                old = self.write_skill()
                with patch.object(
                    skill.os, operation, side_effect=OSError("cannot write")
                ):
                    self.assertIn("synchronization skipped", self.sync())
                self.assertEqual(self.path.read_text(encoding="utf-8"), old)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_revalidation_preserves_concurrent_newer_edit_unmanaged_or_removal(
        self,
    ) -> None:
        for changed in (
            skill.render_skill("99.0"),
            skill.render_skill("0.11.0") + "Edit\n",
            "unmanaged\n",
            None,
        ):
            with self.subTest(changed=changed):
                self.write_skill()

                def change_before_recheck(
                    _fd: int, changed: str | None = changed
                ) -> None:
                    if changed is None:
                        self.path.unlink()
                    else:
                        self.path.write_bytes(changed.encode("utf-8"))

                with (
                    patch.object(skill.os, "fsync", side_effect=change_before_recheck),
                    patch.object(skill.os, "replace") as replace,
                ):
                    self.assertIn("changed during update", self.sync())
                    replace.assert_not_called()
                if changed is None:
                    self.assertFalse(self.path.exists())
                else:
                    self.assertEqual(self.path.read_text(encoding="utf-8"), changed)
                self.assertFalse(list(self.path.parent.glob(".SKILL.*.tmp")))

    def test_malformed_or_ambiguous_front_matter_is_preserved(self) -> None:
        for text in (
            "---\nmetadata: [\n---\nBody\n",
            "---\nmetadata:\n  managed-by: sql-agent-cli\n  managed-by: other\n---\n",
        ):
            self.write_skill(text)
            self.assertIn("synchronization skipped", self.sync())
            self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_unexpected_directories_and_unrelated_files_are_safe(self) -> None:
        self.path.parent.mkdir(parents=True)
        unrelated = self.path.parent / "notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        for force in (False, True):
            with self.assertRaises(UsageError):
                skill.install_skill(force=force)
            with self.assertRaises(UsageError):
                skill.remove_skill(force=force)
        self.write_skill()
        skill.install_skill()
        skill.remove_skill()
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")
        self.assertEqual(list(self.path.parent.iterdir()), [unrelated])

    def test_remove_managed_legacy_and_forced_unmanaged(self) -> None:
        self.assertEqual(skill.remove_skill()["reason"], "not_installed")
        for text in (skill.render_skill(), skill.MANAGED_MARKER + "\nLegacy body\n"):
            self.write_skill(text)
            self.assertTrue(skill.remove_skill()["removed"])
            self.assertFalse(self.path.parent.exists())
        self.write_skill("custom\n")
        with self.assertRaises(UsageError):
            skill.remove_skill()
        self.assertTrue(skill.remove_skill(force=True)["removed"])


class RuntimeSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.module = self.root / "site-packages" / "sql_agent" / "skill.py"
        self.distribution = Mock()
        self.distribution.files = [Path("sql_agent/skill.py")]
        self.distribution.locate_file.side_effect = lambda entry: (
            self.root / "site-packages" / entry
        )
        self.distribution.read_text.return_value = None
        for patcher in (
            patch.object(skill, "__file__", str(self.module)),
            patch.object(
                skill.metadata, "distribution", return_value=self.distribution
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_index_and_wheel_installs_are_eligible(self) -> None:
        for source in (
            None,
            {
                "url": "file:///tmp/sql_agent_cli-0.12.0-py3-none-any.whl",
                "archive_info": {},
            },
            {"url": "https://example.org/sql_agent_cli.whl", "archive_info": {}},
        ):
            self.distribution.read_text.return_value = (
                json.dumps(source) if source else None
            )
            self.assertEqual(skill.runtime_source(), "installed")

    def test_local_direct_editable_and_source_archives_are_excluded(self) -> None:
        for source, expected in (
            ({"url": "file:///project", "dir_info": {}}, "local"),
            ({"url": "file:///project", "dir_info": {"editable": True}}, "editable"),
            ({"url": "file:///project.tar.gz", "archive_info": {}}, "local"),
        ):
            self.distribution.read_text.return_value = json.dumps(source)
            self.assertEqual(skill.runtime_source(), expected)

    def test_checkout_is_excluded_without_metadata_or_with_unrelated_installed_distribution(
        self,
    ) -> None:
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / "pyproject.toml").touch()
        with patch.object(
            skill, "__file__", str(checkout / "src" / "sql_agent" / "skill.py")
        ):
            self.assertEqual(skill.runtime_source(), "local")
            with patch.object(
                skill.metadata,
                "distribution",
                side_effect=metadata.PackageNotFoundError,
            ):
                self.assertEqual(skill.runtime_source(), "local")

    def test_unknown_metadata_or_import_mismatch_is_ineligible(self) -> None:
        self.distribution.read_text.return_value = "{invalid"
        self.assertEqual(skill.runtime_source(), "unknown")
        self.distribution.read_text.return_value = None
        self.distribution.files = None
        self.assertEqual(skill.runtime_source(), "unknown")
        self.distribution.files = [Path("sql_agent/another.py")]
        self.assertEqual(skill.runtime_source(), "unknown")
        with patch.object(
            skill.metadata, "distribution", side_effect=metadata.PackageNotFoundError
        ):
            self.assertEqual(skill.runtime_source(), "unknown")


if __name__ == "__main__":
    unittest.main()
