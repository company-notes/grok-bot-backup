"""Portable ZIP packing: original bytes, UTF-8 names, symlinks and ZIP64."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from grok_bot_backup.archive import create_zip


class ArchiveTests(unittest.TestCase):
    def test_zip_keeps_unicode_and_binary_files_and_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "virtual-office-20261023T000000Z"
            (source / "01-assistants" / "中文助理").mkdir(parents=True)
            payload = "文件、備份、memories".encode("utf-8") + b"\x00\xff"
            (source / "01-assistants" / "中文助理" / "notes.md").write_bytes(payload)
            (source / "empty").mkdir()
            zipfile_path = root / (source.name + ".zip")
            result = create_zip(source, zipfile_path)
            self.assertEqual(result, zipfile_path)
            self.assertEqual(zipfile_path.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / (source.name + ".zip.partial")).exists())
            with zipfile.ZipFile(result) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(
                    archive.read(f"{source.name}/01-assistants/中文助理/notes.md"),
                    payload,
                )
                self.assertIn(f"{source.name}/empty/", archive.namelist())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks not supported")
    def test_zip_preserves_links_without_following_outside_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "grok-bot-export-test"
            source.mkdir()
            secret = root / "external-password.txt"
            secret.write_text("NOT PART OF BACKUP")
            os.symlink(str(secret), source / "pointer-to-outside")
            os.symlink("missing", source / "broken-link")
            zip_path = root / "backup.zip"
            create_zip(source, zip_path)
            with zipfile.ZipFile(zip_path) as archive:
                for name in ("pointer-to-outside", "broken-link"):
                    info = archive.getinfo(f"{source.name}/{name}")
                    self.assertEqual(stat.S_IFMT(info.external_attr >> 16), stat.S_IFLNK)
                self.assertEqual(
                    archive.read(f"{source.name}/pointer-to-outside"),
                    os.fsencode(str(secret)),
                )
                self.assertNotIn(b"NOT PART OF BACKUP",
                                 b"".join(archive.read(n) for n in archive.namelist() if not n.endswith("/")))

    def test_rejects_archive_inside_export_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "backup"
            source.mkdir()
            with self.assertRaises(ValueError):
                create_zip(source, source / "self.zip")
            self.assertFalse((source / "self.zip").exists())


if __name__ == "__main__":
    unittest.main()
