"""Create a Windows-friendly ZIP backup without losing or following symlinks.

The standard-library zipfile writer produces UTF-8 ZIP entries and ZIP64 for
large exports. Symbolic links are encoded with Unix ZIP metadata and their
link targets as bytes; they are NEVER dereferenced while packing.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import stat
import zipfile
from datetime import datetime


def create_zip(source: pathlib.Path, archive: pathlib.Path) -> pathlib.Path:
    """Compress a finished export tree under one top-level directory."""
    source = pathlib.Path(source).resolve()
    archive = pathlib.Path(archive).expanduser().resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"Export directory does not exist: {source}")
    if archive == source or source in archive.parents:
        raise ValueError("The ZIP destination must be outside the exported tree")

    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(archive.name + ".partial")
    if temporary.exists():
        raise FileExistsError(f"Temporary archive already exists: {temporary}")

    def add_node(zf: zipfile.ZipFile, path: pathlib.Path, arcname: str) -> None:
        # Check symlinks before directories or files. ZipFile.write follows
        # links, which would silently include out-of-scope or secret targets.
        if path.is_symlink():
            info = zipfile.ZipInfo(arcname)
            stamp = datetime.fromtimestamp(path.lstat().st_mtime)
            year = max(1980, min(2107, stamp.year))
            info.date_time = (year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | stat.S_IMODE(path.lstat().st_mode)) << 16
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, os.fsencode(os.readlink(path)))
            return
        if path.is_dir():
            zf.write(path, arcname + "/")
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                add_node(zf, child, arcname + "/" + child.name)
            return
        if path.is_file():
            zf.write(path, arcname)
            return
        raise ValueError(f"Unsupported source entry in backup: {path}")

    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=6, allowZip64=True, strict_timestamps=False,
        ) as zf:
            add_node(zf, source, source.name)
        temporary.chmod(0o600)
        os.replace(temporary, archive)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack a completed Grok Bot export into ZIP")
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("archive", type=pathlib.Path)
    args = parser.parse_args()
    out = create_zip(args.source, args.archive)
    print(f"ARCHIVE={out}")
    print(f"ARCHIVE_BYTES={out.stat().st_size}")


if __name__ == "__main__":
    main()
