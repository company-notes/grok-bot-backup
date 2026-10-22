"""Check optional large-workspace-directory inclusion without platform access."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nuwa_backup.workspace import copy_workspace


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
