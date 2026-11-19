"""Regression tests for the PR #2 review fixes.

1. .gitignore keeps every generated backup output (archives, split volumes,
   sidecars, export folders) out of this public repository.
2. generated_backup_artifact() recognises split ZIP volumes and sidecars, so a
   HOME / WORKSPACE copy never re-includes an earlier backup.
3. INCLUDE_HOME=1 with a missing BOX_HOME fails (INCLUDE_HOME=0 is the only
   skip), and the home copy is checked file by file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from grok_bot_backup import home as home_mod
from grok_bot_backup.home import HomeCopyError, copy_home, require_home_source
from grok_bot_backup.verify import verify_home
from grok_bot_backup.workspace import copy_workspace, generated_backup_artifact

REPO = Path(__file__).resolve().parents[1]
STAMP = "20261120T000000Z"
AGENT_ID = "00000000-0000-0000-0000-000000000001"
CLEAN_ENV = {"INCLUDE_HOME": "1", "HOME_EXCLUDE_PATHS": "", "HOME_INCLUDE_PATHS": "",
             "BACKUP_EXCLUDE_PATHS": "", "INCLUDE_BULKY_DIRS": "0"}

GENERATED_FILES = [
    f"grok-bot-export-{STAMP}.zip",
    f"grok-bot-export-{STAMP}.z01",
    f"grok-bot-export-{STAMP}.z02",
    f"grok-bot-export-{STAMP}.z10",
    f"grok-bot-export-{STAMP}.z99",
    f"grok-bot-export-{STAMP}.z100",
    f"grok-bot-export-{STAMP}.Z03",
    f"grok-bot-export-{STAMP}.zip.001",
    f"grok-bot-export-{STAMP}.001",
    f"grok-bot-export-{STAMP}-RESTORE.txt",
    f"grok-bot-export-{STAMP}-SHA256SUMS.txt",
    f"grok-bot-export-{STAMP}.tar.gz",
    f"virtual-office-{STAMP}.zip",
    f"virtual-office-{STAMP}-1of3.zip",
    f"virtual-office-{STAMP}-3of3.z01",
    f"virtual-office-{STAMP}-3of3.z12",
    f"virtual-office-{STAMP}-RESTORE.txt",
    f"virtual-office-{STAMP}-SHA256SUMS.txt",
    f"virtual-office-{STAMP}-CONTENTS.tsv",
]
# Ordinary user files that merely share a prefix or an extension.
USER_FILES = [
    "grok-bot-export-guide.md",
    "grok-bot-export-zones.txt",
    "grok-bot-export-notes.z",
    "grok-bot-export-x.z1",
    "virtual-office-plan.docx",
    "virtual-office-restore-notes.md",
    "virtual-office-CONTENTS-draft.md",
    "report.z01",
    "my-RESTORE.txt",
    "SHA256SUMS",
    "photo.001.jpg",
]


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


@unittest.skipUnless(shutil.which("git") and (REPO / ".git").exists(), "needs a git checkout")
class GitignoreTests(unittest.TestCase):
    def ignored(self, path: str) -> bool:
        result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path],
                                cwd=REPO, capture_output=True, text=True)
        return result.returncode == 0

    def test_generated_outputs_are_ignored_anywhere(self):
        paths = list(GENERATED_FILES) + [
            f"grok-bot-export-{STAMP}/meta/MANIFEST.json",
            f"grok-bot-export-{STAMP}/home/.secrets/service.env",
            f"virtual-office-{STAMP}/00-shared/box-home/.secrets/service.env",
            "exports/memory-drop/shared.json",
            "renamed-backup.z07",
            "renamed-backup.zip.001",
            "copy-SHA256SUMS.txt",
            "copy-RESTORE.txt",
            "copy-CONTENTS.tsv",
        ]
        for base in ("", "nested/dir/"):
            for path in paths:
                with self.subTest(path=base + path):
                    self.assertTrue(self.ignored(base + path))

    def test_tracked_sources_are_not_ignored(self):
        tracked = subprocess.run(["git", "ls-files", "-ci", "--exclude-standard"],
                                 cwd=REPO, capture_output=True, text=True, check=True)
        self.assertEqual(tracked.stdout.strip(), "")
        for path in ("grok_bot_backup/workspace.py", "tests/test_review_fixes.py",
                     "bin/export-grok-bot-backup.sh", "README.md", "CHANGELOG.md"):
            with self.subTest(path=path):
                self.assertFalse(self.ignored(path))


class GeneratedArtifactTests(unittest.TestCase):
    def test_volumes_and_sidecars_are_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in GENERATED_FILES:
                write(root / name)
                with self.subTest(name=name):
                    self.assertIsNotNone(generated_backup_artifact(root / name))
            reasons = {n: generated_backup_artifact(root / n) for n in GENERATED_FILES}
            self.assertIn("volume", reasons[f"grok-bot-export-{STAMP}.z01"])
            self.assertIn("sidecar", reasons[f"virtual-office-{STAMP}-CONTENTS.tsv"])
            self.assertIn("archive", reasons[f"virtual-office-{STAMP}-1of3.zip"])

    def test_ordinary_files_are_not_recognised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in USER_FILES:
                write(root / name)
                with self.subTest(name=name):
                    self.assertIsNone(generated_backup_artifact(root / name))
            # A path that does not exist is never an artifact.
            self.assertIsNone(generated_backup_artifact(root / f"grok-bot-export-{STAMP}.z01x"))

    def test_workspace_and_home_skip_old_volumes_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            workspace = home / "workspace"
            for base in (workspace / "old", home / "moved"):
                for name in GENERATED_FILES + USER_FILES:
                    write(base / name, "old backup data")
            write(workspace / "keep.md", "keep")
            write(home / ".secrets" / "service.env", "TOKEN=placeholder")
            out = root / "out"
            with patch.dict(os.environ, CLEAN_ENV):
                ws_report = copy_workspace(workspace, out / "ws", out)
                home_report = copy_home(home, out / "home", out, workspace, root / "agent-data")
            for dest, sub in ((out / "ws", "old"), (out / "home", "moved")):
                for name in GENERATED_FILES:
                    with self.subTest(dest=dest.name, generated=name):
                        self.assertFalse(os.path.lexists(dest / sub / name))
                for name in USER_FILES:
                    with self.subTest(dest=dest.name, kept=name):
                        self.assertTrue((dest / sub / name).is_file())
            self.assertTrue((out / "ws" / "keep.md").is_file())
            self.assertTrue((out / "home" / ".secrets" / "service.env").is_file())
            self.assertEqual(sum("previous backup" in x for x in ws_report["skipped"]),
                             len(GENERATED_FILES))
            self.assertEqual(sum("previous backup" in x["reason"] for x in home_report["skipped"]),
                             len(GENERATED_FILES))


class HomeRequiredTests(unittest.TestCase):
    def test_include_home_1_with_missing_home_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "no-such-home"
            with patch.dict(os.environ, {**CLEAN_ENV, "BOX_HOME": str(missing)}):
                with self.assertRaises(FileNotFoundError):
                    require_home_source()
                with self.assertRaises(FileNotFoundError):
                    copy_home(missing, root / "out" / "home", root / "out",
                              root / "ws", root / "agent-data")
            write(root / "a-file", "not a directory")
            with patch.dict(os.environ, CLEAN_ENV):
                with self.assertRaises(FileNotFoundError):
                    copy_home(root / "a-file", root / "out" / "home", root / "out",
                              root / "ws", root / "agent-data")
            self.assertFalse((root / "out" / "home").exists())

    def test_include_home_0_with_missing_home_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "no-such-home"
            with patch.dict(os.environ, {**CLEAN_ENV, "INCLUDE_HOME": "0",
                                         "BOX_HOME": str(missing)}):
                require_home_source()
                report = copy_home(missing, root / "out" / "home", root / "out",
                                   root / "ws", root / "agent-data")
            self.assertFalse(report["enabled"])
            write(root / "export" / "meta" / "home-copy.json", json.dumps(report))
            checks = Checks()
            verify_home(root / "export", checks)
            self.assertTrue(checks.ok, checks.results)

    def test_verifier_fails_old_silent_skip_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "meta" / "home-copy.json", json.dumps({
                "enabled": True, "home": "/missing",
                "note": "home directory not found; nothing copied"}))
            checks = Checks()
            verify_home(root, checks)
            self.assertFalse(checks.ok)


class HomeIntegrityTests(unittest.TestCase):
    def make_home(self, root: Path) -> Path:
        home = root / "home"
        write(home / ".secrets" / "service.env", "TOKEN=placeholder")
        write(home / ".config" / "tool.json", "{}")
        write(home / "notes" / "big.bin", "b" * 300_000)
        os.symlink(".config/tool.json", home / "tool-link")
        return home

    def run_copy(self, root: Path, home: Path):
        with patch.dict(os.environ, CLEAN_ENV):
            return copy_home(home, root / "export" / "home", root / "out",
                             root / "ws", root / "agent-data")

    def test_report_records_hashes_and_verifier_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            report = self.run_copy(root, home)
            self.assertEqual(set(report["file_hashes"]),
                             {".secrets/service.env", ".config/tool.json", "notes/big.bin"})
            self.assertEqual(report["file_hashes"]["notes/big.bin"]["bytes"], 300_000)
            self.assertEqual(report["bytes"], sum(h["bytes"] for h in report["file_hashes"].values()))
            export = root / "export"
            write(export / "meta" / "home-copy.json", json.dumps(report))
            dst = export / "home"

            checks = Checks()
            verify_home(export, checks)
            self.assertTrue(checks.ok, checks.results)

            # Same size, different content: only the hash check can see it.
            (dst / ".secrets" / "service.env").write_text("TOKEN=placeholdeX")
            checks = Checks()
            verify_home(export, checks)
            self.assertFalse(checks.ok)
            self.assertTrue(any("content differs" in m for ok, m in checks.results if not ok))
            (dst / ".secrets" / "service.env").write_text("TOKEN=placeholder")

            # Extra file not recorded in the report.
            write(dst / "unexpected.txt", "x")
            checks = Checks()
            verify_home(export, checks)
            self.assertFalse(checks.ok)
            (dst / "unexpected.txt").unlink()

            # Truncated file: byte total and hash both differ.
            (dst / "notes" / "big.bin").write_text("b" * 10)
            checks = Checks()
            verify_home(export, checks)
            self.assertFalse(checks.ok)

    def test_copy_mismatch_fails_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            with patch.object(home_mod, "sha256_file", return_value="0" * 64):
                with self.assertRaises(HomeCopyError) as caught:
                    self.run_copy(root, home)
            self.assertIn("does not match source", str(caught.exception))

    def test_copy_write_error_fails_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            with patch.object(home_mod.shutil, "copystat", side_effect=OSError(28, "No space left")):
                with self.assertRaises(HomeCopyError) as caught:
                    self.run_copy(root, home)
            self.assertIn("copy failed", str(caught.exception))

    def test_tree_audit_mismatch_fails_the_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = self.make_home(root)
            with patch.object(home_mod, "audit_copy",
                              return_value={"files": 0, "symlinks": 0, "bytes": 0}):
                with self.assertRaises(HomeCopyError) as caught:
                    self.run_copy(root, home)
            self.assertIn("copied tree has", str(caught.exception))


class ExportCommandHomeTests(unittest.TestCase):
    """Run the real export commands against a tiny fake box."""

    def make_box(self, root: Path) -> dict:
        data = root / "agent-data"
        (data / "agents" / AGENT_ID).mkdir(parents=True)
        (data / "agents" / AGENT_ID / "profile.json").write_text('{"name":"Test assistant"}')
        conn = sqlite3.connect(data / "search-index.db")
        conn.execute("create table agents (agent_id text)")
        conn.execute("create table messages (id integer primary key, agent_id text, seq integer,"
                     " entry_id text, role text, timestamp_ms integer, body text)")
        conn.execute("create table media (id integer primary key, agent_id text, seq integer,"
                     " entry_id text, timestamp_ms integer, file_name text, kind text, ext text)")
        conn.execute("insert into agents (agent_id) values (?)", (AGENT_ID,))
        conn.execute("insert into messages (agent_id, seq, entry_id, role, timestamp_ms, body)"
                     " values (?, 1, 'm1', 'user', 1792600000000, 'Hello')", (AGENT_ID,))
        conn.commit()
        conn.close()
        (root / "workspace").mkdir()
        env = os.environ.copy()
        env.update({
            "AGENT_DATA": str(data), "WORKSPACE": str(root / "workspace"),
            "OUT_ROOT": str(root / "workspace" / "exports"),
            "MEMORY_DROP": str(root / "workspace" / "empty-memory-drop"),
            "BACKUP_EXCLUDE_PATHS": "", "INCLUDE_BULKY_DIRS": "0",
            "BOX_HOME": str(root / "missing-home"),
            "HOME_EXCLUDE_PATHS": "", "HOME_INCLUDE_PATHS": "", "ARCHIVE_SPLIT_SIZE": "",
        })
        return env

    def run_cmd(self, args, env):
        return subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, timeout=60)

    def test_missing_home_fails_both_exporters_when_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**self.make_box(root), "INCLUDE_HOME": "1"}
            raw = self.run_cmd([str(REPO / "bin" / "export-grok-bot-backup.sh")], env)
            self.assertNotEqual(raw.returncode, 0, raw.stdout + raw.stderr)
            self.assertIn("home directory not found", raw.stderr)
            readable = self.run_cmd([str(REPO / "bin" / "export-virtual-office.sh")], env)
            self.assertNotEqual(readable.returncode, 0, readable.stdout + readable.stderr)
            exports = root / "workspace" / "exports"
            self.assertEqual(list(exports.glob("*.zip")) if exports.exists() else [], [])

    def test_missing_home_is_skipped_when_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**self.make_box(root), "INCLUDE_HOME": "0"}
            raw = self.run_cmd([str(REPO / "bin" / "export-grok-bot-backup.sh")], env)
            self.assertEqual(raw.returncode, 0, raw.stdout + raw.stderr)
            folder = next(p for p in (root / "workspace" / "exports").glob("grok-bot-export-*")
                          if p.is_dir())
            report = json.loads((folder / "meta" / "home-copy.json").read_text())
            self.assertFalse(report["enabled"])
            verified = self.run_cmd([str(REPO / "bin" / "verify-export.sh"), str(folder),
                                     "--archive", str(folder.with_suffix(".zip"))], env)
            self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
            self.assertIn("RESULT OK", verified.stdout)


if __name__ == "__main__":
    unittest.main()
