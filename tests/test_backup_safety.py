"""Safety regression tests: never copy a backup into its own workspace export."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grok_bot_backup.common import valid_agent_id
from grok_bot_backup.conversations import index_agent_ids
from grok_bot_backup.workspace import copy_workspace
from grok_bot_backup import virtual_office


class BackupSafetyTests(unittest.TestCase):
    def test_virtual_office_does_not_recursively_copy_nested_out_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            output = workspace / "custom" / "archives"
            old = workspace / "exports"
            (old / "old-backup").mkdir(parents=True)
            (old / "old-backup" / "large.fake").write_text("old")
            (workspace / "secrets").mkdir(parents=True)
            (workspace / "secrets" / "credential.txt").write_text("sensitive")
            (workspace / "docs").mkdir()
            (workspace / "docs" / "note.md").write_text("note")

            with patch.dict(os.environ, {"INCLUDE_BULKY_DIRS": "0", "BACKUP_EXCLUDE_PATHS": ""}):
                with patch.object(virtual_office, "WORKSPACE", workspace), \
                     patch.object(virtual_office, "OUT_ROOT", output), \
                     patch.object(virtual_office, "AGENT_DATA", root / "agent-data"):
                    report = virtual_office.export_shared(output / "virtual-office" / "00-shared")

            exported = output / "virtual-office" / "00-shared" / "workspace-outputs"
            self.assertTrue((exported / "secrets" / "credential.txt").is_file())
            self.assertTrue((exported / "docs" / "note.md").is_file())
            self.assertFalse((exported / "exports").exists())
            self.assertFalse((exported / "custom" / "archives").exists())
            self.assertTrue(any("backup output" in x for x in report["skipped"]))
            self.assertTrue(any("default backup directory" in x for x in report["skipped"]))
            self.assertLess(sum(1 for _ in exported.rglob("*")), 15)

    def test_excludes_are_optional_and_workspace_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "workspace"
            (source / "secrets").mkdir(parents=True)
            (source / "secrets" / "token").write_text("value")
            (source / "regular").mkdir()
            (source / "regular" / "note").write_text("keep")
            with patch.dict(os.environ, {"BACKUP_EXCLUDE_PATHS": "secrets"}):
                report = copy_workspace(source, Path(tmp) / "dest", source / "exports")
            self.assertFalse((Path(tmp) / "dest/secrets").exists())
            self.assertTrue((Path(tmp) / "dest/regular/note").is_file())
            self.assertTrue(any("configured exclusion" in x for x in report["skipped"]))

    def test_rejects_output_root_containing_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "workspace"
            source.mkdir()
            with self.assertRaises(ValueError):
                copy_workspace(source, source / "target", Path(tmp))

    def test_platform_non_assistant_ids_do_not_create_conversation_folders(self):
        db = sqlite3.connect(":memory:")
        db.execute("create table agents (agent_id text)")
        db.execute("create table messages (agent_id text)")
        real_id = "00000000-0000-0000-0000-000000000001"
        db.executemany("insert into agents values (?)", [
            ("organization",), ("people",), ("procedures",),
            (real_id,), (None,),
        ])
        db.executemany("insert into messages values (?)", [
            ("organization",), ("people",), (real_id,),
        ])
        self.assertEqual(index_agent_ids(db), [real_id])
        db.close()

    def test_non_agent_named_folders_are_not_agents(self):
        self.assertFalse(valid_agent_id("organization"))
        self.assertFalse(valid_agent_id("people"))
        self.assertTrue(valid_agent_id("00000000-0000-0000-0000-000000000001"))


if __name__ == "__main__":
    unittest.main()
