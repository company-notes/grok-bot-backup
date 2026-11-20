"""Home-directory copy: hidden credential folders kept, caches and duplicates omitted."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grok_bot_backup.home import copy_home
from grok_bot_backup.verify import verify_home

CLEAN_ENV = {"INCLUDE_HOME": "1", "HOME_EXCLUDE_PATHS": "", "HOME_INCLUDE_PATHS": "",
             "BACKUP_EXCLUDE_PATHS": "", "INCLUDE_BULKY_DIRS": "0"}


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Checks:
    def __init__(self):
        self.results = []

    def __call__(self, cond, msg):
        self.results.append((bool(cond), msg))

    @property
    def ok(self):
        return all(c for c, _ in self.results)


class HomeCopyTests(unittest.TestCase):
    def make_home(self, root: Path):
        home = root / "home" / "box"
        write(home / ".secrets" / "service.env", "TOKEN=placeholder")
        write(home / ".assistant-a" / "password.txt", "placeholder")
        write(home / ".config" / "tool" / "settings.json", "{}")
        write(home / ".mcp-auth" / "client" / "tokens.json", "{}")
        write(home / ".gitconfig", "[user]")
        write(home / ".local" / "share" / "keyrings" / "login.keyring", "k")
        write(home / ".local" / "lib" / "python3" / "pkg.py", "p" * 1000)
        write(home / ".cache" / "blob", "c" * 500)
        write(home / ".npm" / "_cacache" / "x", "n")
        write(home / "chrome-profile" / "Default" / "Cookies", "b" * 200)
        write(home / "tools" / "node_modules" / "dep" / "index.js", "m")
        write(home / "tools" / "run.sh", "echo hi")
        write(home / "sand-data" / "agents" / "a" / "profile.json", "{}")
        os.symlink("sand-data", home / "agent-data")
        write(home / "workspace" / "doc.md", "doc")
        write(home / "workspace" / "exports" / "old.txt", "old")
        write(home / "grok-bot-export-20260101T000000Z.zip", "old archive")
        os.symlink("/nonexistent/target", home / "broken-link")
        os.symlink(".gitconfig", home / "gitconfig-link")
        return home

    def run_copy(self, root: Path, home: Path, **env):
        with patch.dict(os.environ, {**CLEAN_ENV, **env}):
            return copy_home(home, root / "out" / "export" / "home", root / "out",
                             home / "workspace", home / "agent-data")

    def test_copies_hidden_settings_and_omits_caches_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            report = self.run_copy(root, home)
            dst = root / "out" / "export" / "home"
            for kept in (".secrets/service.env", ".assistant-a/password.txt",
                         ".config/tool/settings.json", ".mcp-auth/client/tokens.json",
                         ".gitconfig", ".local/share/keyrings/login.keyring", "tools/run.sh"):
                with self.subTest(kept=kept):
                    self.assertTrue((dst / kept).is_file())
            for omitted in (".cache", ".npm", ".local/lib", "chrome-profile", "agent-data",
                            "sand-data", "workspace", "tools/node_modules",
                            "grok-bot-export-20260101T000000Z.zip"):
                with self.subTest(omitted=omitted):
                    self.assertFalse(os.path.lexists(dst / omitted))
            # Symlinks are preserved as links and not followed.
            self.assertTrue((dst / "broken-link").is_symlink())
            self.assertEqual(os.readlink(dst / "gitconfig-link"), ".gitconfig")
            reasons = {x["path"]: x["reason"] for x in report["skipped"]}
            self.assertIn("AGENT_DATA", reasons["agent-data"])
            self.assertIn("AGENT_DATA", reasons["sand-data"])
            self.assertIn("WORKSPACE", reasons["workspace"])
            self.assertIn("default exclusion", reasons["chrome-profile"])
            self.assertIn("bulky", reasons["tools/node_modules"])
            self.assertIn("previous backup", reasons["grok-bot-export-20260101T000000Z.zip"])
            sizes = {x["path"]: x["bytes"] for x in report["skipped"]}
            self.assertEqual(sizes[".cache"], 500)
            self.assertEqual(report["files"], 7)
            self.assertEqual(report["symlinks"], 2)
            json.dumps(report)
            export_root = root / "out" / "export"
            write(export_root / "meta" / "home-copy.json", json.dumps(report))
            checks = Checks()
            verify_home(export_root, checks)
            self.assertTrue(checks.ok, checks.results)
            (dst / "tools" / "run.sh").unlink()
            checks = Checks()
            verify_home(export_root, checks)
            self.assertFalse(checks.ok)

    def test_include_and_exclude_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            self.run_copy(root, home, HOME_INCLUDE_PATHS="chrome-profile",
                          HOME_EXCLUDE_PATHS=".assistant-a:tools/run.sh")
            dst = root / "out" / "export" / "home"
            self.assertTrue((dst / "chrome-profile" / "Default" / "Cookies").is_file())
            self.assertFalse((dst / ".assistant-a").exists())
            self.assertFalse((dst / "tools" / "run.sh").exists())
            with self.assertRaises(ValueError):
                self.run_copy(root, home, HOME_INCLUDE_PATHS=".secrets")
            with self.assertRaises(ValueError):
                self.run_copy(root, home, HOME_EXCLUDE_PATHS=".")

    def test_disabled_copies_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            report = self.run_copy(root, home, INCLUDE_HOME="0")
            self.assertFalse(report["enabled"])
            self.assertFalse((root / "out" / "export" / "home").exists())
            with self.assertRaises(ValueError):
                self.run_copy(root, home, INCLUDE_HOME="yes")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can read everything")
    def test_unreadable_files_are_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            write(home / "locked" / "a.txt", "a")
            write(home / "locked-file.txt", "b")
            (home / "locked").chmod(0)
            (home / "locked-file.txt").chmod(0)
            try:
                report = self.run_copy(root, home)
            finally:
                (home / "locked").chmod(0o700)
                (home / "locked-file.txt").chmod(0o600)
            paths = {x["path"] for x in report["unreadable"]}
            self.assertEqual(paths, {"locked/", "locked-file.txt"})
            self.assertTrue((root / "out" / "export" / "home" / ".secrets" / "service.env").is_file())

    def test_rejects_output_root_containing_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            with patch.dict(os.environ, CLEAN_ENV):
                with self.assertRaises(ValueError):
                    copy_home(home, root / "dest", root, home / "workspace", home / "agent-data")


if __name__ == "__main__":
    unittest.main()
