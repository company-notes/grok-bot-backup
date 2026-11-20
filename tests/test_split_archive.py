"""Size-limited archives: standard split ZIP (raw) and independent ZIP packs (readable).

External tools are optional. When found on PATH (or via SEVEN_ZIP), 7-Zip
tests the split archive and every pack directly, and Info-ZIP `unzip -t` tests
the joined result and ordinary packs.
"""
from __future__ import annotations

import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile

from grok_bot_backup.archive import (
    MIN_SPLIT_SIZE, _iter_nodes, create_split_zip, create_zip_packs, is_split_zip,
    join_split_zip, parse_size, split_parts,
)
from grok_bot_backup.verify import verify_archive

MiB = 1024 * 1024
SEVEN_ZIP = os.environ.get("SEVEN_ZIP") or shutil.which("7zz") or shutil.which("7z")
UNZIP = shutil.which("unzip")


def make_tree(root: Path, name: str = "grok-bot-export-20261119T000000Z", big: int = 0) -> Path:
    rng = random.Random(1234)
    source = root / name
    (source / "01-assistants" / "\u4e2d\u6587").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "99-backup-program").mkdir()
    (source / "99-backup-program" / "COMMIT").write_text("0" * 40 + "\n")
    (source / "index.html").write_text('<a href="01-assistants/page-0099.html">last</a>')
    for i in range(400):
        text = " ".join(rng.choice(["alpha", "beta", "\u5099\u4efd", str(i)]) for _ in range(800))
        (source / "01-assistants" / f"page-{i:04d}.html").write_text(text, encoding="utf-8")
    (source / "01-assistants" / "\u4e2d\u6587" / "noise.bin").write_bytes(rng.randbytes(int(2.5 * MiB)))
    if big:
        (source / "huge-recording.bin").write_bytes(rng.randbytes(big))
    if hasattr(os, "symlink"):
        os.symlink("index.html", source / "link-to-index")
    return source


def expected_names(source: Path) -> set[str]:
    return {arc + ("/" if p.is_dir() and not p.is_symlink() else "") for p, arc in _iter_nodes(source)}


def read_all(zip_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.testzip() is None
        return {n: zf.read(n) for n in zf.namelist()}


class Checks:
    def __init__(self):
        self.results = []

    def __call__(self, cond, msg):
        self.results.append((bool(cond), msg))

    @property
    def ok(self):
        return all(c for c, _ in self.results)


class ParseSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertIsNone(parse_size(""))
        self.assertIsNone(parse_size("0"))
        self.assertIsNone(parse_size(None))
        self.assertEqual(parse_size("90MiB"), 90 * MiB)
        self.assertEqual(parse_size("90M"), 90 * MiB)
        self.assertEqual(parse_size("95MB"), 95_000_000)
        self.assertEqual(parse_size("1g"), 1024 ** 3)
        self.assertEqual(parse_size("123456"), 123456)
        with self.assertRaises(ValueError):
            parse_size("1000")  # below the 64 KiB segment minimum
        with self.assertRaises(ValueError):
            parse_size("ninety")


class SplitZipTests(unittest.TestCase):
    def test_no_limit_or_small_archive_stays_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root)
            for size in (None, 64 * MiB):
                out = root / f"single-{size}.zip"
                result = create_split_zip(source, out, size)
                self.assertEqual(result.layout, "single")
                self.assertEqual(result.files, [out.resolve()])
                self.assertEqual(result.sidecars, [])
                self.assertFalse(is_split_zip(out))
                self.assertEqual(set(read_all(out)), expected_names(source))
            self.assertEqual(sorted(p.name for p in root.glob("*-RESTORE.txt")), [])

    def test_split_archive_parts_and_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root)
            archive = root / "out" / (source.name + ".zip")
            result = create_split_zip(source, archive, MiB)
            self.assertEqual(result.layout, "split")
            names = [p.name for p in result.files]
            stem = source.name
            self.assertEqual(names[-1], stem + ".zip")
            self.assertEqual(names[:-1], [f"{stem}.z{i:02d}" for i in range(1, len(names))])
            self.assertGreaterEqual(len(names), 3)
            for p in result.files:
                self.assertLessEqual(p.stat().st_size, MiB)
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            for p in result.files[:-1]:
                self.assertEqual(p.stat().st_size, MiB)  # full segments, like zip -s
            self.assertEqual(result.files[0].read_bytes()[:4], struct.pack("<I", 0x08074B50))
            self.assertEqual(split_parts(archive), result.files)
            self.assertTrue(is_split_zip(archive))
            self.assertFalse((archive.parent / (archive.name + ".single")).exists())
            restore = archive.parent / f"{stem}-RESTORE.txt"
            self.assertIn(restore, result.sidecars)
            text = restore.read_text()
            self.assertIn("7-Zip", text)
            self.assertIn(f"{stem}.z01", text)
            joined = join_split_zip(archive, root / "joined.zip")
            contents = read_all(joined)
            self.assertEqual(set(contents), expected_names(source))
            noise = source / "01-assistants" / "\u4e2d\u6587" / "noise.bin"
            self.assertEqual(contents[f"{stem}/01-assistants/\u4e2d\u6587/noise.bin"], noise.read_bytes())
            checks = Checks()
            verify_archive(archive, source, checks)
            self.assertTrue(checks.ok, checks.results)
            self.assertTrue(any("SHA256SUMS match" in m for _, m in checks.results))
            self.assertTrue(any("archive members match" in m for _, m in checks.results))
            if SEVEN_ZIP:
                run = subprocess.run([SEVEN_ZIP, "t", str(archive)], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn("Everything is Ok", run.stdout)
            if UNZIP:
                run = subprocess.run([UNZIP, "-tq", str(joined)], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_corrupt_or_missing_part_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root)
            archive = root / "out" / "x.zip"
            result = create_split_zip(source, archive, MiB)
            second = result.files[1]
            data = bytearray(second.read_bytes())
            data[len(data) // 2] ^= 0xFF
            second.write_bytes(bytes(data))
            checks = Checks()
            verify_archive(archive, source, checks)
            self.assertFalse(checks.ok)
            second.unlink()
            checks = Checks()
            verify_archive(archive, source, checks)
            self.assertFalse(checks.ok)

    def test_central_directory_spanning_segments_and_zip64_entry_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "many"
            source.mkdir()
            for i in range(66000):  # > 65535 entries forces ZIP64 end records
                (source / f"{i:05d}-a-long-name-to-enlarge-the-central-directory.txt").write_text(str(i))
            archive = root / "many.zip"
            result = create_split_zip(source, archive, MIN_SPLIT_SIZE)
            self.assertGreater(len(result.files), 40)
            joined = join_split_zip(archive, root / "joined.zip")
            with zipfile.ZipFile(joined) as zf:
                self.assertEqual(len(zf.namelist()), 66001)
                self.assertIsNone(zf.testzip())
            if SEVEN_ZIP:
                run = subprocess.run([SEVEN_ZIP, "t", str(archive)], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class PackTests(unittest.TestCase):
    def test_packs_are_independent_complete_zips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root, "virtual-office-20261119T000000Z", big=int(1.5 * MiB))
            archive = root / "out" / (source.name + ".zip")
            result = create_zip_packs(source, archive, MiB)
            self.assertEqual(result.layout, "packs")
            self.assertFalse(archive.exists())
            packs = sorted({p.with_suffix(".zip") for p in result.files})
            total = len(packs)
            self.assertGreaterEqual(total, 3)
            self.assertEqual({p.name for p in packs},
                             {f"{source.name}-{i}of{total}.zip" for i in range(1, total + 1)})
            for p in result.files:
                self.assertLessEqual(p.stat().st_size, MiB)
            merged = root / "merged"
            merged.mkdir()
            seen = []
            oversized = {f"{source.name}/huge-recording.bin",
                         f"{source.name}/01-assistants/\u4e2d\u6587/noise.bin"}
            for pack in packs:
                if is_split_zip(pack):
                    # only a single file larger than the limit is ever split
                    joined = join_split_zip(pack, root / ("j-" + pack.name))
                    names = list(read_all(joined))
                    self.assertEqual(len(names), 1)
                    self.assertIn(names[0], oversized)
                    zipfile.ZipFile(joined).extractall(merged)
                else:
                    names = list(read_all(pack))  # opens on its own
                    self.assertTrue(all(n.startswith(source.name + "/") for n in names))
                    zipfile.ZipFile(pack).extractall(merged)
                seen += names
            self.assertEqual(len(seen), len(set(seen)))
            self.assertEqual(set(seen), expected_names(source))
            self.assertTrue((merged / source.name / "index.html").is_file())
            self.assertTrue((merged / source.name / "01-assistants" / "page-0099.html").is_file())
            restore = archive.parent / f"{source.name}-RESTORE.txt"
            contents = archive.parent / f"{source.name}-CONTENTS.tsv"
            self.assertIn("SAME destination folder", restore.read_text())
            self.assertIn("split ZIP", restore.read_text())
            rows = contents.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows) - 1, len(expected_names(source)))
            self.assertTrue(any("huge-recording.bin" in r and "split ZIP" in r for r in rows))
            checks = Checks()
            verify_archive(packs[1], source, checks)  # any pack name finds the whole set
            self.assertTrue(checks.ok, checks.results)
            if SEVEN_ZIP:
                for pack in packs:
                    run = subprocess.run([SEVEN_ZIP, "t", str(pack)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            if UNZIP:
                for pack in packs:
                    if not is_split_zip(pack):
                        run = subprocess.run([UNZIP, "-tq", str(pack)], capture_output=True, text=True)
                        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_missing_pack_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root, "virtual-office-x")
            archive = root / "out" / (source.name + ".zip")
            result = create_zip_packs(source, archive, MiB)
            result.files[0].unlink()
            checks = Checks()
            verify_archive(result.files[-1], source, checks)
            self.assertFalse(checks.ok)

    def test_no_limit_keeps_single_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = make_tree(root, "virtual-office-y")
            archive = root / (source.name + ".zip")
            result = create_zip_packs(source, archive, None)
            self.assertEqual(result.layout, "single")
            self.assertEqual(result.files, [archive.resolve()])
            self.assertEqual(set(read_all(archive)), expected_names(source))
            self.assertEqual(list(root.glob("*-RESTORE.txt")), [])


if __name__ == "__main__":
    unittest.main()
