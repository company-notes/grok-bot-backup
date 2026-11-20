"""Create a Windows-friendly ZIP backup without losing or following symlinks.

The standard-library zipfile writer produces UTF-8 ZIP entries and ZIP64 for
large exports. Symbolic links are encoded with Unix ZIP metadata and their
link targets as bytes; they are NEVER dereferenced while packing.

Optional size limit (for upload services with a per-file cap)
-------------------------------------------------------------
``ARCHIVE_SPLIT_SIZE`` (or ``--split-size``) sets the largest allowed output
file. When it is unset, a single ``.zip`` is written exactly as before. When a
finished archive is larger than the limit, one of two layouts is produced:

* ``split_zip``: a standard *split ZIP archive* (PKWARE APPNOTE 8.5, the format
  written by Info-ZIP ``zip -s``): ``name.z01``, ``name.z02`` ... ``name.zip``.
  It is written in pure Python, so no extra package is needed to create it.
  7-Zip and WinRAR open it directly; ``python3 -m grok_bot_backup.archive join``
  rebuilds an ordinary ZIP; Windows File Explorer cannot.
* ``create_zip_packs``: several independent, complete ZIP files
  (``name-1of3.zip`` ...). Each opens on its own; extracting all of them into
  the same folder rebuilds the full tree. A single file that cannot fit in one
  pack is stored alone in a pack that is itself a split ZIP archive.

Whenever output is divided, a plain-text ``*-RESTORE.txt`` and a
``*-SHA256SUMS.txt`` are written next to the archive files.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import stat
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime

SPLIT_ENV = "ARCHIVE_SPLIT_SIZE"
MIN_SPLIT_SIZE = 64 * 1024  # APPNOTE 8.5.2: split segments are at least 64 KiB
COMPRESSLEVEL = 6

_SIG_SPLIT = 0x08074B50
_SIG_LOCAL = 0x04034B50
_SIG_CENTRAL = 0x02014B50
_SIG_EOCD = 0x06054B50
_SIG_ZIP64_EOCD = 0x06064B50
_SIG_ZIP64_LOCATOR = 0x07064B50
_U16 = 0xFFFF
_U32 = 0xFFFFFFFF

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?b?)\s*$", re.I)


def parse_size(text: str | int | None) -> int | None:
    """Parse ``90MiB``, ``90M``, ``100MB`` or a byte count. Empty/0 = no limit.

    ``K/M/G/T`` and ``KiB/MiB/GiB/TiB`` are powers of 1024;
    ``KB/MB/GB/TB`` are powers of 1000.
    """
    if text is None:
        return None
    if isinstance(text, int):
        value = text
    else:
        m = _SIZE_RE.match(text)
        if not text.strip():
            return None
        if not m:
            raise ValueError(f"Invalid size: {text!r} (examples: 90MiB, 95000000)")
        number, unit, suffix = m.group(1), m.group(2).lower(), m.group(3).lower()
        power = "kmgt".index(unit) + 1 if unit else 0
        base = 1000 if suffix == "b" and unit else 1024
        value = int(float(number) * (base ** power))
    if value <= 0:
        return None
    if value < MIN_SPLIT_SIZE:
        raise ValueError(f"Split size must be at least {MIN_SPLIT_SIZE} bytes (64 KiB)")
    return value


def split_size_from_env() -> int | None:
    return parse_size(os.environ.get(SPLIT_ENV, ""))


# ---------------------------------------------------------------- packing

def _iter_nodes(source: pathlib.Path):
    """Yield (path, arcname) in archive order: directory entry, then children."""
    def walk(path: pathlib.Path, arcname: str):
        yield path, arcname
        if path.is_dir() and not path.is_symlink():
            for child in sorted(path.iterdir(), key=lambda p: p.name):
                yield from walk(child, arcname + "/" + child.name)
    yield from walk(source, source.name)


def _add_node(zf: zipfile.ZipFile, path: pathlib.Path, arcname: str) -> None:
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
        return
    if path.is_file():
        zf.write(path, arcname)
        return
    raise ValueError(f"Unsupported source entry in backup: {path}")


def _open_zip(path: pathlib.Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED,
        compresslevel=COMPRESSLEVEL, allowZip64=True, strict_timestamps=False,
    )


def _check_paths(source: pathlib.Path, archive: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    source = pathlib.Path(source).resolve()
    archive = pathlib.Path(archive).expanduser().resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"Export directory does not exist: {source}")
    if archive == source or source in archive.parents:
        raise ValueError("The ZIP destination must be outside the exported tree")
    archive.parent.mkdir(parents=True, exist_ok=True)
    return source, archive


def _write_zip(nodes, archive: pathlib.Path) -> pathlib.Path:
    temporary = archive.with_name(archive.name + ".partial")
    if temporary.exists():
        raise FileExistsError(f"Temporary archive already exists: {temporary}")
    try:
        with _open_zip(temporary) as zf:
            for path, arcname in nodes:
                _add_node(zf, path, arcname)
        temporary.chmod(0o600)
        os.replace(temporary, archive)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return archive


def create_zip(source: pathlib.Path, archive: pathlib.Path) -> pathlib.Path:
    """Compress a finished export tree under one top-level directory."""
    source, archive = _check_paths(source, archive)
    return _write_zip(_iter_nodes(source), archive)


# ------------------------------------------------------ raw ZIP structures

@dataclass
class _Entry:
    made_by: int
    needed: int
    flags: int
    method: int
    mtime: int
    mdate: int
    crc: int
    csize: int
    usize: int
    name: bytes
    extra: bytes          # without the ZIP64 (0x0001) field
    comment: bytes
    disk: int
    iattr: int
    eattr: int
    offset: int


def _strip_zip64(extra: bytes) -> tuple[bytes, bytes]:
    kept, z64, i = b"", b"", 0
    while i + 4 <= len(extra):
        tag, size = struct.unpack_from("<HH", extra, i)
        chunk = extra[i:i + 4 + size]
        if tag == 0x0001:
            z64 = extra[i + 4:i + 4 + size]
        else:
            kept += chunk
        i += 4 + size
    return kept, z64


def _parse_central(data: bytes, count: int) -> list[_Entry]:
    entries, pos = [], 0
    for _ in range(count):
        (sig, made_by, needed, flags, method, mtime, mdate, crc, csize, usize,
         nlen, xlen, clen, disk, iattr, eattr, offset) = struct.unpack_from(
            "<IHHHHHHIIIHHHHHII", data, pos)
        if sig != _SIG_CENTRAL:
            raise zipfile.BadZipFile("Bad central directory record")
        pos += 46
        name = data[pos:pos + nlen]; pos += nlen
        extra, z64 = _strip_zip64(data[pos:pos + xlen]); pos += xlen
        comment = data[pos:pos + clen]; pos += clen
        z = 0
        def take(fmt, size):
            nonlocal z
            (value,) = struct.unpack_from(fmt, z64, z)
            z += size
            return value
        if usize == _U32: usize = take("<Q", 8)
        if csize == _U32: csize = take("<Q", 8)
        if offset == _U32: offset = take("<Q", 8)
        if disk == _U16: disk = take("<I", 4)
        entries.append(_Entry(made_by, needed, flags, method, mtime, mdate, crc, csize, usize,
                              name, extra, comment, disk, iattr, eattr, offset))
    return entries


def _central_record(e: _Entry, disk: int, offset: int) -> bytes:
    z64 = b""
    usize, csize, off, dsk = e.usize, e.csize, offset, disk
    if usize >= _U32: z64 += struct.pack("<Q", usize); usize = _U32
    if csize >= _U32: z64 += struct.pack("<Q", csize); csize = _U32
    if off >= _U32: z64 += struct.pack("<Q", off); off = _U32
    if dsk >= _U16: z64 += struct.pack("<I", dsk); dsk = _U16
    extra = (struct.pack("<HH", 1, len(z64)) + z64 if z64 else b"") + e.extra
    needed = max(e.needed, 45) if z64 else e.needed
    return struct.pack("<IHHHHHHIIIHHHHHII", _SIG_CENTRAL, e.made_by, needed, e.flags, e.method,
                       e.mtime, e.mdate, e.crc, csize, usize, len(e.name), len(extra),
                       len(e.comment), dsk, e.iattr, e.eattr, off) + e.name + extra + e.comment


def _read_tail(fp, size: int) -> tuple[bytes, int]:
    start = max(0, size - (22 + 0xFFFF + 20 + 56))
    fp.seek(start)
    return fp.read(), start


def _read_directory_info(last_segment: pathlib.Path):
    """Read EOCD (and ZIP64 EOCD) from the last segment of an archive."""
    size = last_segment.stat().st_size
    with open(last_segment, "rb") as fp:
        tail, start = _read_tail(fp, size)
        i = tail.rfind(struct.pack("<I", _SIG_EOCD))
        if i < 0:
            raise zipfile.BadZipFile(f"No end of central directory in {last_segment}")
        (_, disk, cd_disk, n_disk, n_total, cd_size, cd_offset, clen) = struct.unpack_from(
            "<IHHHHIIH", tail, i)
        comment = tail[i + 22:i + 22 + clen]
        disks = disk + 1
        if i >= 20 and struct.unpack_from("<I", tail, i - 20)[0] == _SIG_ZIP64_LOCATOR:
            _, z_disk, z_off, total_disks = struct.unpack_from("<IIQI", tail, i - 20)
            fp.seek(z_off)
            rec = fp.read(56)
            (sig, _, _, _, disk64, cd_disk, n_disk, n_total, cd_size, cd_offset) = struct.unpack(
                "<IQHHIIQQQQ", rec)
            if sig != _SIG_ZIP64_EOCD:
                raise zipfile.BadZipFile("Bad ZIP64 end of central directory")
            disks = total_disks
        return disks, cd_disk, cd_offset, cd_size, n_total, comment


def split_segment_names(archive: pathlib.Path, disks: int) -> list[pathlib.Path]:
    archive = pathlib.Path(archive)
    stem = archive.with_suffix("")
    return [stem.with_name(f"{stem.name}.z{i + 1:02d}") for i in range(disks - 1)] + [archive]


def split_parts(archive: pathlib.Path) -> list[pathlib.Path]:
    """All files of an archive: [archive] or [name.z01, ..., name.zip]."""
    disks = _read_directory_info(pathlib.Path(archive))[0]
    return split_segment_names(pathlib.Path(archive), disks)


def is_split_zip(archive: pathlib.Path) -> bool:
    try:
        return _read_directory_info(pathlib.Path(archive))[0] > 1
    except (OSError, zipfile.BadZipFile, struct.error):
        return False


class _Concat:
    """Read-only view of several segment files as one byte stream."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.starts, total = [], 0
        for p in self.paths:
            self.starts.append(total)
            total += p.stat().st_size
        self.size = total

    def read_at(self, pos: int, length: int) -> bytes:
        out = bytearray()
        for i, p in enumerate(self.paths):
            seg_start = self.starts[i]
            seg_end = seg_start + p.stat().st_size
            if pos >= seg_end or pos + length <= seg_start:
                continue
            with open(p, "rb") as fp:
                fp.seek(max(pos, seg_start) - seg_start)
                out += fp.read(min(pos + length, seg_end) - max(pos, seg_start))
        return bytes(out)

    def copy_to(self, out, pos: int, end: int, chunk: int = 1 << 20) -> None:
        while pos < end:
            data = self.read_at(pos, min(chunk, end - pos))
            if not data:
                raise zipfile.BadZipFile("Unexpected end of split archive")
            out.write(data)
            pos += len(data)


def _eocd_block(disk: int, cd_disk: int, n_disk: int, n_total: int, cd_size: int,
                cd_offset: int, total_disks: int, z64_offset: int, comment: bytes = b"") -> bytes:
    need64 = (disk >= _U16 or cd_disk >= _U16 or n_disk >= _U16 or n_total >= _U16
              or cd_size >= _U32 or cd_offset >= _U32)
    block = b""
    if need64:
        block += struct.pack("<IQHHIIQQQQ", _SIG_ZIP64_EOCD, 44, 45, 45, disk, cd_disk,
                             n_disk, n_total, cd_size, cd_offset)
        block += struct.pack("<IIQI", _SIG_ZIP64_LOCATOR, disk, z64_offset, total_disks)
    block += struct.pack("<IHHHHIIH", _SIG_EOCD, min(disk, _U16), min(cd_disk, _U16),
                         min(n_disk, _U16), min(n_total, _U16), min(cd_size, _U32),
                         min(cd_offset, _U32), len(comment)) + comment
    return block


class _SegmentWriter:
    def __init__(self, archive: pathlib.Path, size: int):
        self.archive, self.size = archive, size
        self.disk, self.pos, self.fp = 0, 0, None
        self.paths: list[pathlib.Path] = []
        self._open()
        self.write(struct.pack("<I", _SIG_SPLIT))

    def _open(self):
        if self.fp:
            self.fp.close()
        stem = self.archive.with_suffix("")
        path = stem.with_name(f"{stem.name}.z{self.disk + 1:02d}")
        if path.exists():
            raise FileExistsError(f"Split part already exists: {path}")
        self.paths.append(path)
        self.fp = open(path, "xb")
        os.chmod(path, 0o600)
        self.pos = 0

    def next_disk(self):
        self.disk += 1
        self._open()

    def ensure(self, length: int):
        """Start a new segment unless `length` bytes fit unsplit in this one."""
        if length > self.size:
            raise ValueError(f"A {length}-byte ZIP record cannot fit in a {self.size}-byte segment")
        if self.pos + length > self.size:
            self.next_disk()

    def write(self, data: bytes):
        view = memoryview(data)
        while view:
            if self.pos >= self.size:
                self.next_disk()
            n = min(len(view), self.size - self.pos)
            self.fp.write(view[:n])
            self.pos += n
            view = view[n:]

    def finish(self) -> list[pathlib.Path]:
        self.fp.close()
        os.replace(self.paths[-1], self.archive)
        self.paths[-1] = self.archive
        return self.paths


def split_zip(archive: pathlib.Path, size: int) -> list[pathlib.Path]:
    """Convert a single ZIP into a standard split ZIP (name.z01 ... name.zip).

    Local headers and central-directory records are never divided between
    segments; file data may continue across segments, as in Info-ZIP.
    Returns all part paths in order (the last one is ``archive``).
    """
    archive = pathlib.Path(archive)
    size = parse_size(size)
    if size is None:
        raise ValueError("A positive split size is required")
    disks, cd_disk, cd_offset, cd_size, n_total, comment = _read_directory_info(archive)
    if disks != 1:
        raise ValueError(f"Already a split archive: {archive}")
    if archive.stat().st_size <= size:
        return [archive]
    source = archive.with_name(archive.name + ".single")
    if source.exists():
        raise FileExistsError(f"Temporary archive already exists: {source}")
    os.replace(archive, source)
    writer = None
    try:
        with open(source, "rb") as fp:
            fp.seek(cd_offset)
            entries = _parse_central(fp.read(cd_size), n_total)
            order = sorted(range(len(entries)), key=lambda k: entries[k].offset)
            ends = [entries[k].offset for k in order[1:]] + [cd_offset]
            writer = _SegmentWriter(archive, size)
            placed: dict[int, tuple[int, int]] = {}
            for k, end in zip(order, ends):
                start = entries[k].offset
                fp.seek(start)
                head = fp.read(30)
                if len(head) != 30 or struct.unpack_from("<I", head)[0] != _SIG_LOCAL:
                    raise zipfile.BadZipFile("Bad local file header")
                nlen, xlen = struct.unpack_from("<HH", head, 26)
                head += fp.read(nlen + xlen)
                writer.ensure(len(head))
                placed[k] = (writer.disk, writer.pos)
                writer.write(head)
                remaining = end - start - len(head)
                while remaining > 0:
                    chunk = fp.read(min(1 << 20, remaining))
                    if not chunk:
                        raise zipfile.BadZipFile("Truncated ZIP data")
                    writer.write(chunk)
                    remaining -= len(chunk)
        new_cd_disk = new_cd_offset = None
        new_cd_size = 0
        record_disks = []
        for k, e in enumerate(entries):
            record = _central_record(e, *placed[k])
            writer.ensure(len(record))
            if new_cd_disk is None:
                new_cd_disk, new_cd_offset = writer.disk, writer.pos
            record_disks.append(writer.disk)
            writer.write(record)
            new_cd_size += len(record)
        if new_cd_disk is None:
            new_cd_disk, new_cd_offset = writer.disk, writer.pos
        on_last_disk = record_disks.count(writer.disk)
        # Keep ZIP64 EOCD, locator and EOCD together in the final segment.
        probe = _eocd_block(writer.disk + 1, new_cd_disk, on_last_disk, len(entries),
                            new_cd_size, new_cd_offset, writer.disk + 2, 0, comment)
        if writer.pos + len(probe) > size:
            writer.next_disk()
            on_last_disk = 0
        block = _eocd_block(writer.disk, new_cd_disk, on_last_disk, len(entries), new_cd_size,
                            new_cd_offset, writer.disk + 1, writer.pos, comment)
        writer.write(block)
        if writer.disk == 0:
            raise RuntimeError("internal error: split produced a single segment")
        parts = writer.finish()
        source.unlink()
        return parts
    except BaseException:
        if writer and writer.fp:
            writer.fp.close()
        if writer:
            for p in writer.paths:
                p.unlink(missing_ok=True)
        if source.exists():
            os.replace(source, archive)
        raise


def join_split_zip(archive: pathlib.Path, output: pathlib.Path) -> pathlib.Path:
    """Rebuild a single ordinary ZIP from a split archive (like ``zip -s 0``)."""
    archive, output = pathlib.Path(archive), pathlib.Path(output)
    disks, cd_disk, cd_offset, cd_size, n_total, comment = _read_directory_info(archive)
    parts = split_segment_names(archive, disks)
    missing = [p.name for p in parts if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing split part(s): " + ", ".join(missing))
    stream = _Concat(parts)
    skip = 4 if disks > 1 and stream.read_at(0, 4) == struct.pack("<I", _SIG_SPLIT) else 0
    cd_abs = stream.starts[cd_disk] + cd_offset
    entries = _parse_central(stream.read_at(cd_abs, cd_size), n_total)
    with open(output, "xb") as out:
        os.chmod(output, 0o600)
        stream.copy_to(out, skip, cd_abs)
        cd = b"".join(_central_record(e, 0, stream.starts[e.disk] + e.offset - skip) for e in entries)
        new_cd_offset = out.tell()
        out.write(cd)
        out.write(_eocd_block(0, 0, len(entries), len(entries), len(cd), new_cd_offset, 1,
                              out.tell(), comment))
    return output


# ----------------------------------------------- independent ZIP "packs"

_LOCAL_OVERHEAD = 30 + 20 + 16      # header + possible ZIP64 extra + slack
_CENTRAL_OVERHEAD = 46 + 28 + 16
_ARCHIVE_OVERHEAD = 22 + 56 + 20 + 1024


def _compressed_size(path: pathlib.Path) -> int:
    comp = zlib.compressobj(COMPRESSLEVEL, zlib.DEFLATED, -15)
    total = 0
    with open(path, "rb") as fp:
        while chunk := fp.read(1 << 20):
            total += len(comp.compress(chunk))
    return total + len(comp.flush())


def _node_cost(path: pathlib.Path, arcname: str) -> int:
    name = len(arcname.encode("utf-8")) + 1
    if path.is_symlink():
        data = len(os.fsencode(os.readlink(path)))
    elif path.is_dir():
        data = 0
    else:
        data = _compressed_size(path)
    return data + _LOCAL_OVERHEAD + _CENTRAL_OVERHEAD + 2 * name


@dataclass
class Pack:
    nodes: list = field(default_factory=list)
    cost: int = _ARCHIVE_OVERHEAD
    oversized: bool = False


def plan_packs(source: pathlib.Path, size: int) -> list[Pack]:
    """Fill packs in archive order; start a new pack when the next entry won't fit.

    An entry that cannot fit in any pack gets a pack of its own (written later
    as a split ZIP); ordinary packs keep filling around it. Oversized packs are
    numbered after the ordinary ones.
    """
    packs: list[Pack] = [Pack()]
    oversized: list[Pack] = []
    for path, arcname in _iter_nodes(pathlib.Path(source)):
        cost = _node_cost(path, arcname)
        if _ARCHIVE_OVERHEAD + cost > size:
            oversized.append(Pack([(path, arcname)], _ARCHIVE_OVERHEAD + cost, oversized=True))
            continue
        if packs[-1].cost + cost > size:
            packs.append(Pack())
        packs[-1].nodes.append((path, arcname))
        packs[-1].cost += cost
    return [p for p in packs if p.nodes] + oversized


def pack_name(archive: pathlib.Path, index: int, total: int) -> pathlib.Path:
    stem = pathlib.Path(archive).with_suffix("")
    return stem.with_name(f"{stem.name}-{index}of{total}.zip")


def sidecar(archive: pathlib.Path, suffix: str) -> pathlib.Path:
    stem = pathlib.Path(archive).with_suffix("")
    return stem.with_name(f"{stem.name}-{suffix}")


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        while chunk := fp.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256sums(archive: pathlib.Path, files) -> pathlib.Path:
    target = sidecar(archive, "SHA256SUMS.txt")
    lines = [f"{sha256_file(p)}  {p.name}\n" for p in files]
    target.write_text("".join(lines), encoding="utf-8")
    target.chmod(0o600)
    return target


def _human(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MiB"


def _split_restore_text(archive: pathlib.Path, parts, size: int) -> str:
    first, last = parts[0].name, archive.name
    joined = archive.with_suffix("").name + "-joined.zip"
    lines = "\n".join(f"  {p.name}" for p in parts)
    return f"""HOW TO OPEN {archive.name} (split ZIP archive)

This backup is larger than the configured per-file limit ({size} bytes,
{_human(size)}), so it was saved as ONE standard split ("spanned") ZIP archive
in {len(parts)} parts. A single part cannot be opened on its own.

Parts:
{lines}

1. Download ALL parts into the same folder. Do not rename them.
2. Open the .zip part ({last}), not {first}:
   - Windows: 7-Zip (https://www.7-zip.org/) or WinRAR -> open {last} -> Extract.
     Windows File Explorer's built-in "Extract All" cannot open split ZIPs.
   - macOS / Linux: 7zz x {last}
   - Or join the parts into one ordinary ZIP first with this project, then
     use any ZIP tool (including Windows File Explorer):
       python3 -m grok_bot_backup.archive join {last} {joined}
       unzip {joined}
     Do not rely on Info-ZIP "zip -s 0" or "zip -FF" to join: Info-ZIP 3.0
     can truncate a file whose data spans more than two parts.
3. Optional integrity check of the downloaded parts:
     sha256sum -c {sidecar(archive, 'SHA256SUMS.txt').name}

The archive contains unencrypted backup data. Keep it private.
"""


def write_split_restore(archive: pathlib.Path, parts, size: int) -> pathlib.Path:
    target = sidecar(archive, "RESTORE.txt")
    target.write_text(_split_restore_text(archive, parts, size), encoding="utf-8")
    target.chmod(0o600)
    return target


@dataclass
class ArchiveResult:
    files: list[pathlib.Path]         # every archive file to keep/upload, in order
    sidecars: list[pathlib.Path]      # RESTORE / SHA256SUMS / CONTENTS text files
    layout: str                       # "single", "split" or "packs"

    @property
    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files)


def create_split_zip(source: pathlib.Path, archive: pathlib.Path,
                     size: int | None) -> ArchiveResult:
    """Raw export: one ZIP, or a standard split ZIP when larger than `size`."""
    created = create_zip(source, archive)
    if size is None or created.stat().st_size <= size:
        return ArchiveResult([created], [], "single")
    parts = split_zip(created, size)
    sums = write_sha256sums(archive, parts)
    restore = write_split_restore(archive, parts, size)
    return ArchiveResult(parts, [restore, sums], "split")


def create_zip_packs(source: pathlib.Path, archive: pathlib.Path,
                     size: int | None) -> ArchiveResult:
    """Readable export: one ZIP, or several complete ZIPs no larger than `size`."""
    source, archive = _check_paths(source, archive)
    created = create_zip(source, archive)
    if size is None or created.stat().st_size <= size:
        return ArchiveResult([created], [], "single")
    created.unlink()
    plan = plan_packs(source, size)
    total = len(plan)
    files: list[pathlib.Path] = []
    rows = []
    try:
        for index, pack in enumerate(plan, 1):
            target = pack_name(archive, index, total)
            _write_zip(pack.nodes, target)
            if pack.oversized and target.stat().st_size > size:
                parts = split_zip(target, size)
            else:
                parts = [target]
                if target.stat().st_size > size:
                    raise RuntimeError(f"internal error: {target.name} exceeds {size} bytes")
            files.extend(parts)
            label = target.name + (f" (split ZIP, {len(parts)} parts)" if len(parts) > 1 else "")
            for path, arcname in pack.nodes:
                kind = "link" if path.is_symlink() else "dir" if path.is_dir() else "file"
                rows.append(f"{index}\t{label}\t{kind}\t{arcname}{'/' if kind == 'dir' else ''}")
    except BaseException:
        for p in files:
            p.unlink(missing_ok=True)
        raise
    contents = sidecar(archive, "CONTENTS.tsv")
    contents.write_text("pack\tarchive\ttype\tpath\n" + "\n".join(rows) + "\n", encoding="utf-8")
    contents.chmod(0o600)
    sums = write_sha256sums(archive, files)
    restore = sidecar(archive, "RESTORE.txt")
    split_packs = [r for r in rows if "(split ZIP" in r.split("\t")[1]]
    pack_lines = []
    for index in range(1, total + 1):
        target = pack_name(archive, index, total)
        group = [p for p in files if p.with_suffix("").name == target.with_suffix("").name]
        extra = "" if len(group) == 1 else "  <- split ZIP: " + ", ".join(p.name for p in group)
        pack_lines.append(f"  {target.name}{extra}")
    split_note = ""
    if split_packs:
        split_note = f"""
Some single files are larger than the limit. Each of them is stored alone in a
pack that is a standard split ZIP (.z01, .z02 ... .zip). Open those with 7-Zip
or WinRAR (open the .zip part), or join them into one ordinary ZIP first with
  python3 -m grok_bot_backup.archive join NAME.zip NAME-joined.zip
"""
    restore.write_text(f"""HOW TO OPEN {archive.with_suffix('').name} (Virtual Office in {total} ZIP files)

The readable backup is larger than the configured per-file limit ({size} bytes,
{_human(size)}), so it was divided into {total} complete ZIP files. Each ZIP
opens on its own (Windows: right-click -> Extract All).

Files:
{chr(10).join(pack_lines)}

1. Download all ZIP files into the same folder.
2. Extract EVERY ZIP into the SAME destination folder. They all contain the same
   top-level folder ({source.name}/), so their contents merge into one tree and
   links between pages keep working.
3. Open {source.name}/README.md (and other .md / .html files) from the merged folder.
{split_note}
{contents.name} lists which file is in which ZIP.
Optional integrity check: sha256sum -c {sums.name}

The archive contains unencrypted backup data. Keep it private.
""", encoding="utf-8")
    restore.chmod(0o600)
    return ArchiveResult(files, [restore, contents, sums], "packs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack a completed Grok Bot export into ZIP")
    sub = parser.add_subparsers(dest="command")
    join = sub.add_parser("join", help="Join a split ZIP (name.z01 ... name.zip) into one ZIP")
    join.add_argument("archive", type=pathlib.Path)
    join.add_argument("output", type=pathlib.Path)
    if len(sys.argv) > 1 and sys.argv[1] == "join":
        args = parser.parse_args()
        out = join_split_zip(args.archive, args.output)
        print(f"JOINED={out}")
        return
    parser = argparse.ArgumentParser(description="Pack a completed Grok Bot export into ZIP")
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("archive", type=pathlib.Path)
    parser.add_argument("--split-size", default=os.environ.get(SPLIT_ENV, ""),
                        help=f"Largest output file, e.g. 90MiB (default: ${SPLIT_ENV}; empty = no limit)")
    parser.add_argument("--layout", choices=["split", "packs"], default="split",
                        help="split: one standard split ZIP; packs: several complete ZIPs")
    args = parser.parse_args()
    size = parse_size(args.split_size)
    make = create_split_zip if args.layout == "split" else create_zip_packs
    result = make(args.source, args.archive, size)
    print_result(result, args.archive)


def print_result(result: ArchiveResult, archive: pathlib.Path) -> None:
    print(f"ARCHIVE={result.files[0] if result.layout == 'packs' else archive}")
    print(f"ARCHIVE_LAYOUT={result.layout}")
    print(f"ARCHIVE_BYTES={result.total_bytes}")
    for p in result.files:
        print(f"ARCHIVE_PART={p}\t{p.stat().st_size}")
    for p in result.sidecars:
        print(f"ARCHIVE_SIDECAR={p}")


if __name__ == "__main__":
    main()
