"""Check optional large-workspace-directory inclusion without platform access."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grok_bot_backup.workspace import copy_workspace


class WorkspaceBackupTests(unittest.TestCase):
    def test_default_excludes_bulky_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "workspace"
            (src / "proj" / ".venv").mkdir(parents=True)
            (src / "proj" / ".venv" / "pkg.py").write_text("x")
            (src / "proj" / "keep.md").write_text("important")
            (src / "exports").mkdir()
            (src / "exports" / "old.txt").write_text("not recursively copied")
            with patch.dict(os.environ, {"INCLUDE_BULKY_DIRS": "0"}):
                report = copy_workspace(src, Path(tmp) / "dest", src / "exports")
            self.assertTrue((Path(tmp) / "dest/proj/keep.md").exists())
            self.assertFalse((Path(tmp) / "dest/proj/.venv").exists())
            self.assertFalse((Path(tmp) / "dest/exports").exists())
            self.assertEqual(len(report["skipped"]), 2)

    def test_old_exports_are_skipped_with_custom_out_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "workspace"
            out_root = src / "custom" / "new-output"
            (src / "old" / "grok-bot-export-20261009T100000Z").mkdir(parents=True)
            (src / "old" / "grok-bot-export-20261009T100000Z" / "huge.bin").write_text("old raw")
            (src / "archives").mkdir(parents=True)
            (src / "archives" / "virtual-office-20261008T200000Z.tar.gz").write_text("old tar")
            (src / "virtual-office-20260909T120000Z").mkdir()
            (src / "virtual-office-20260909T120000Z" / "old.txt").write_text("old human-readable")
            (src / "grok-bot-export-guide.md").write_text("user document, not an archive")
            (src / "keep").mkdir()
            (src / "keep" / "document.txt").write_text("keep")
            (src / "exports").mkdir()
            (src / "exports" / "old-backup.tar.gz").write_text("exclude default exports")
            with patch.dict(os.environ, {"INCLUDE_BULKY_DIRS": "1", "BACKUP_EXCLUDE_PATHS": ""}):
                report = copy_workspace(src, Path(tmp) / "dest", out_root)
            dest = Path(tmp) / "dest"
            self.assertTrue((dest / "keep" / "document.txt").exists())
            self.assertTrue((dest / "grok-bot-export-guide.md").exists())
            self.assertFalse((dest / "old" / "grok-bot-export-20261009T100000Z").exists())
            self.assertFalse((dest / "archives" / "virtual-office-20261008T200000Z.tar.gz").exists())
            self.assertFalse((dest / "virtual-office-20260909T120000Z").exists())
            self.assertFalse((dest / "exports").exists())
            self.assertTrue(any("previous backup directory" in x for x in report["skipped"]))
            self.assertTrue(any("previous backup archive" in x for x in report["skipped"]))

    def test_old_generated_dirs_are_excluded_even_with_include_bulky(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "workspace"
            (src / ".cache").mkdir(parents=True)
            (src / ".cache" / "c").write_text("cache")
            (src / "virtual-office-old-test").mkdir()
            (src / "virtual-office-old-test" / "data").write_text("old backup")
            with patch.dict(os.environ, {"INCLUDE_BULKY_DIRS": "1", "BACKUP_EXCLUDE_PATHS": ""}):
                report = copy_workspace(src, Path(tmp) / "dest", src / "exports")
            self.assertTrue((Path(tmp) / "dest" / ".cache" / "c").exists())
            self.assertFalse((Path(tmp) / "dest" / "virtual-office-old-test").exists())
            self.assertTrue(any("previous backup directory" in x for x in report["skipped"]))

    def test_explicit_include_copies_nested_bulky(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "workspace"
            (src / "project" / "node_modules").mkdir(parents=True)
            (src / "project" / "node_modules" / "package.js").write_text("ok")
            (src / "exports").mkdir()
            with patch.dict(os.environ, {"INCLUDE_BULKY_DIRS": "1"}):
                report = copy_workspace(src, Path(tmp) / "dest", src / "exports")
            self.assertTrue((Path(tmp) / "dest/project/node_modules/package.js").exists())
            self.assertEqual(report["skipped"], ["exports (backup output)"])


if __name__ == "__main__":
    unittest.main()
