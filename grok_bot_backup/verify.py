"""Verify a finished export: conversations vs search index, program copy, notes completeness.

usage: python3 -m grok_bot_backup.verify <export-dir> [--archive X.zip] [--date YYYY-MM-DD]
Prints a short report; exit code 1 if any check fails. Never prints message bodies.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
import pathlib
import re
import sqlite3
import tarfile
import tempfile
import zipfile

import hashlib

from .archive import (
    _iter_nodes, is_split_zip, join_split_zip, sha256_file, sidecar, split_parts,
)
from .common import AGENT_DATA, SEARCH_INDEX, TAIPEI

ENTRY_RE = re.compile(r"^<!-- entry:(\S+) seq:(\S+) -->$", re.M)
HDR_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} · ", re.M)


PACK_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d+)of(?P<total>\d+)\.zip$")


def _zip_names(path: pathlib.Path, check, label: str) -> list[str]:
    """Test one ZIP (joining it first if it is a split archive); return member names."""
    if is_split_zip(path):
        parts = split_parts(path)
        missing = [p.name for p in parts if not p.is_file()]
        check(not missing, f"{label}: split ZIP has all {len(parts)} parts"
              + ("" if not missing else " (missing " + ", ".join(missing) + ")"))
        if missing:
            return []
        with tempfile.TemporaryDirectory(prefix="verify-split-") as tmp:
            joined = join_split_zip(path, pathlib.Path(tmp) / "joined.zip")
            return _zip_names(joined, check, label + " (joined)")
    with zipfile.ZipFile(path) as zipped:
        names = zipped.namelist()
        corrupt = zipped.testzip()
    check(corrupt is None,
          f"{label}: ZIP data integrity" if corrupt is None else f"{label}: corrupt ZIP entry: {corrupt}")
    return names


def verify_archive(archive_path: pathlib.Path, root: pathlib.Path, check) -> None:
    """Check a single ZIP, a split ZIP (name.z01 ... name.zip) or a set of ZIP packs."""
    try:
        sums_base = archive_path
        match = PACK_RE.match(archive_path.name)
        if match:
            total = int(match.group("total"))
            sums_base = archive_path.with_name(match.group("stem") + ".zip")
            packs = [archive_path.with_name(f"{match.group('stem')}-{i}of{total}.zip")
                     for i in range(1, total + 1)]
            missing = [p.name for p in packs if not p.is_file()]
            check(not missing, f"all {total} ZIP packs present"
                  + ("" if not missing else " (missing " + ", ".join(missing) + ")"))
            names = []
            for pack in packs:
                if pack.is_file():
                    names += _zip_names(pack, check, pack.name)
            print(f"INFO Archive layout: {total} independent ZIP packs")
        elif zipfile.is_zipfile(archive_path) or is_split_zip(archive_path):
            if is_split_zip(archive_path):
                print(f"INFO Archive layout: split ZIP in {len(split_parts(archive_path))} parts")
            names = _zip_names(archive_path, check, archive_path.name)
        elif tarfile.is_tarfile(archive_path):
            # Existing .tar.gz archives remain verifiable for compatibility.
            with tarfile.open(archive_path, "r:*") as tar:
                names = tar.getnames()
            print("INFO Legacy tar archive: ZIP is now the default output")
            check(any(n.endswith("/COMMIT") for n in names),
                  f"archive contains program COMMIT ({len(names)} members)")
            return
        else:
            raise ValueError("not a readable ZIP or legacy tar archive")
        check(any(n.endswith("/COMMIT") for n in names),
              f"archive contains program COMMIT ({len(names)} members)")
        check(len(names) == len(set(names)), "no duplicate archive members")
        if root.is_dir():
            expected = {arc + ("/" if p.is_dir() and not p.is_symlink() else "")
                        for p, arc in _iter_nodes(root.resolve())}
            got = set(names)
            check(got == expected,
                  f"archive members match export folder ({len(got)} archive / {len(expected)} folder"
                  + (f"; missing {len(expected - got)}, extra {len(got - expected)}" if got != expected else "")
                  + ")")
        sums = sidecar(sums_base, "SHA256SUMS.txt")
        if sums.is_file():
            bad = []
            lines = [l for l in sums.read_text(encoding="utf-8").splitlines() if l.strip()]
            for line in lines:
                digest, name = line.split(None, 1)
                target = sums.with_name(name.strip().lstrip("*"))
                if not target.is_file() or sha256_file(target) != digest:
                    bad.append(target.name)
            check(not bad, f"SHA256SUMS match ({len(lines) - len(bad)}/{len(lines)})"
                  + ("" if not bad else " bad: " + ", ".join(bad)))
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError, RuntimeError) as exc:
        check(False, f"cannot read archive: {exc}")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        while chunk := fp.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_home(root: pathlib.Path, check) -> None:
    """Compare the copied home directory with its copy report (and the live home if present)."""
    report_path = next((p for p in (root / "meta" / "home-copy.json", root / "home-copy.json")
                        if p.is_file()), None)
    if report_path is None:
        print("SKIP HOME: no home-copy.json (older export)")
        return
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("enabled"):
        print(f"SKIP HOME: {report.get('note', 'INCLUDE_HOME=0')}")
        return
    # INCLUDE_HOME=1 must always produce a copy; older exports wrote a note and
    # skipped silently when BOX_HOME was missing.
    check(not report.get("note") and "files" in report,
          "home directory copied (INCLUDE_HOME=1)"
          + (f": {report['note']}" if report.get("note") else ""))
    if report.get("note") or "files" not in report:
        return
    copied = next((p for p in (root / "home", root / "00-shared" / "box-home") if p.is_dir()), None)
    check(copied is not None, "home directory copy present")
    if copied is None:
        return
    files = links = size = 0
    present: dict[str, pathlib.Path] = {}
    for parent, dirs, names in os.walk(copied):
        for name in dirs + names:
            path = pathlib.Path(parent) / name
            if path.is_symlink():
                links += 1
            elif name in names and path.is_file():
                files += 1
                size += path.stat().st_size
                present[path.relative_to(copied).as_posix()] = path
    same_bytes = "bytes" not in report or size == report["bytes"]
    check(files == report["files"] and links == report["symlinks"] and same_bytes,
          f"home copy matches report ({files} files / {links} links / {size} bytes; report "
          f"{report['files']} / {report['symlinks']} / {report.get('bytes', '?')})")
    hashes = report.get("file_hashes")
    if hashes is not None:
        bad = []
        for rel_path, want in hashes.items():
            path = present.get(rel_path)
            if path is None:
                bad.append(f"{rel_path} (missing)")
            elif path.stat().st_size != want["bytes"] or _sha256(path) != want["sha256"]:
                bad.append(f"{rel_path} (content differs)")
        extra = [f"{p} (not in report)" for p in sorted(set(present) - set(hashes))]
        problems = bad + extra
        check(not problems, f"home files match recorded size and SHA-256 "
                            f"({len(hashes) - len(bad)}/{len(hashes)})"
              + ("" if not problems else " bad: " + ", ".join(problems[:10])))
    skipped = report.get("skipped", [])
    unreadable = report.get("unreadable", [])
    print(f"INFO HOME {report['home']}: {len(skipped)} omitted, {len(unreadable)} unreadable "
          f"(see {report_path.relative_to(root)})")
    for item in unreadable[:20]:
        print(f"WARN HOME unreadable, skipped: {item['path']} ({item['error']})")
    live = pathlib.Path(report.get("home_resolved") or report["home"])
    if live.is_dir():
        accounted = {x["path"].split("/", 1)[0] for x in skipped if "/" not in x["path"]}
        accounted |= {x["path"].rstrip("/") for x in unreadable if "/" not in x["path"].rstrip("/")}
        missing = sorted(p.name for p in live.iterdir()
                         if p.name not in accounted and not os.path.lexists(copied / p.name))
        # Live home can change after the backup, so a difference is a warning only.
        if missing:
            print("WARN HOME entries now in the live home but not in the backup "
                  "(new since the backup?): " + ", ".join(missing[:10]))
        else:
            print("PASS every top-level home entry copied or listed as omitted")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--archive")
    ap.add_argument("--date", help="Optional day to require at least one message (YYYY-MM-DD)")
    ap.add_argument("--strict", action="store_true",
                    help="Fail if memory drops are missing, empty without attestation, malformed or stale")
    a = ap.parse_args()
    root = pathlib.Path(a.export_dir)
    ok = True
    max_age_days = float(os.environ.get("MEMORY_MAX_AGE_DAYS", "2"))
    if max_age_days < 0:
        raise SystemExit("MEMORY_MAX_AGE_DAYS cannot be negative")

    def warn(message):
        nonlocal ok
        if a.strict:
            ok = False
            print("FAIL " + message)
        else:
            print("WARN " + message)

    def check(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + msg)

    stats_file = next((p for p in [root / "99-backup-program" / "conversation-stats.json",
                                   root / "conversations" / "conversation-stats.json"] if p.exists()), None)
    check(stats_file is not None, "conversation stats file present")
    if stats_file is None:
        raise SystemExit(1)
    st = json.loads(stats_file.read_text(encoding="utf-8"))
    cutoff = st["cutoff_ms"]
    db = sqlite3.connect(f"file:{SEARCH_INDEX}?mode=ro", uri=True)

    total_md = total_idx = on_date = 0
    for ag in st["agents"]:
        conv = root / ag["conv_dir"]
        md_ids = []
        dates = []
        for md in sorted(conv.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].md")):
            t = md.read_text(encoding="utf-8")
            md_ids += [m.group(1) for m in ENTRY_RE.finditer(t)]
            dates += [m.group(1) for m in HDR_RE.finditer(t)]
        idx_ids = [r[0] for r in db.execute(
            "select entry_id from messages where agent_id=? and timestamp_ms<=?", (ag["agent_id"], cutoff))]
        n_date = dates.count(a.date) if a.date else 0
        total_md += len(md_ids); total_idx += len(idx_ids); on_date += n_date
        same = sorted(md_ids) == sorted(idx_ids) and len(md_ids) == ag["messages"]
        day_detail = f"; {a.date}: {n_date}" if a.date else ""
        check(same, f"{ag['name']}: md {len(md_ids)} / index {len(idx_ids)} / stats {ag['messages']}"
                    f"{day_detail}; range {ag['first']} ~ {ag['last']}")
        if ag["messages"]:
            check((conv / "README.md").exists(), f"{ag['name']}: per-agent index README.md")
    check(total_md == total_idx, f"TOTAL md {total_md} == index {total_idx} (cutoff {st['cutoff_taipei']})")
    if a.date:
        check(on_date > 0, f"messages on {a.date} present: {on_date}")

    # program copy
    prog = next((p for p in [root / "99-backup-program", root / "meta" / "backup-program"] if (p / "COMMIT").exists()), None)
    commit = (prog / "COMMIT").read_text().strip() if prog else ""
    check(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), f"program COMMIT {commit[:12]}")
    check(prog is not None and (prog / "source" / "grok_bot_backup" / "common.py").exists(), "program source present")

    verify_home(root, check)

    if a.archive:
        verify_archive(pathlib.Path(a.archive), root, check)

    # full backup: every source notes/ file must have a byte-identical copy in the export
    def sha(p: pathlib.Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    exported = {sha(p) for p in root.rglob("*") if p.is_file() and p.stat().st_size}
    src = [p for p in (AGENT_DATA / "agents").glob("*/notes/**/*") if p.is_file() and p.stat().st_size]
    missing = [p for p in src if sha(p) not in exported]
    check(not missing, f"all notes files included ({len(src) - len(missing)}/{len(src)})")
    for p in missing[:20]:
        print(f"     missing: {p.relative_to(AGENT_DATA)}")
    # Memory freshness is based on the original file's modification time.
    # shutil.copy2 preserves it in the export, so no live source is required.
    # Snapshot cutoff is used instead of current wall time for repeatable checks.
    mem_index = next((p for p in (
        root / "memory-index.md",
        root / "platform-memory" / "memory-index.md",
    ) if p.exists()), None)
    check(mem_index is not None, "memory-index.md present")
    if mem_index:
        content = mem_index.read_text(encoding="utf-8")
        check("not exported" in content or "exported" in content,
              "memory index includes export status")

    cutoff_seconds = cutoff / 1000

    def export_memory_dir(agent):
        if (root / "conversations").exists():
            return root / "agents" / agent["agent_id"] / "platform-memory"
        return (root / agent["conv_dir"]).parent / "memory"

    def memory_age(timestamp, label):
        age = (cutoff_seconds - timestamp) / 86400
        stamp = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        print(f"MEMORY {label}: file modified (UTC): {stamp}; age at backup {age:.2f} days")
        if age > max_age_days:
            warn(f"{label}: memory export older than {max_age_days:g} days ({age:.2f} days)")
        if age < -0.01:
            warn(f"{label}: memory export timestamp is later than backup cutoff")

    def confirm_explicit_empty(path, label, agent_id=None):
        """A recent agent declaration is needed to distinguish zero facts from no export."""
        try:
            declaration = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(declaration, dict) or declaration.get("status") != "no_memories":
                raise ValueError("expected status: no_memories")
            if agent_id is not None:
                if declaration.get("agent_id") != agent_id:
                    raise ValueError("agent_id does not match the assistant")
            elif declaration.get("scope") != "user":
                raise ValueError("shared-memory declaration requires scope: user")
            checked = declaration.get("checked_at_utc")
            if not isinstance(checked, str):
                raise ValueError("missing checked_at_utc")
            checked_dt = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if checked_dt.tzinfo is None:
                raise ValueError("checked_at_utc must contain a timezone")
            checked_ts = checked_dt.timestamp()
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            warn(f"{label}: invalid memory-status.json ({exc})")
            return
        age = (cutoff_seconds - checked_ts) / 86400
        print(f"MEMORY {label}: explicitly declared no memories; checked at "
              f"{checked_dt.astimezone(timezone.utc).isoformat()} (UTC); "
              f"age at backup {age:.2f} days")
        if age > max_age_days:
            warn(f"{label}: no-memories declaration older than {max_age_days:g} days")
        if age < -0.01:
            warn(f"{label}: no-memories declaration is later than backup cutoff")

    def verify_memory_set(directory, label, agent_id=None):
        files = sorted(directory.glob("*.jsonl"))
        status = directory / "memory-status.json"
        if not files and not status.is_file():
            warn(f"{label}: no platform memory export found")
            return

        facts = 0
        invalid = 0
        file_ages = []
        for file in files:
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or not str(record.get("fact") or "").strip():
                        invalid += 1
                    else:
                        facts += 1
                except json.JSONDecodeError:
                    invalid += 1
            age = (cutoff_seconds - file.stat().st_mtime) / 86400
            print(f"MEMORY {label}: {file.name}, file modified (UTC): "
                  f"{datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat()}, "
                  f"age at backup {age:.2f} days")
            file_ages.append((file.name, age))

        # If there are no records, file timestamps are not proof of a completed
        # empty export. Use the explicit checked_at_utc declaration instead.
        # Otherwise check freshness on the actual nonempty JSONL source files.
        if facts:
            for filename, age in file_ages:
                if age > max_age_days:
                    warn(f"{label}: {filename} older than {max_age_days:g} days ({age:.2f} days)")
                if age < -0.01:
                    warn(f"{label}: {filename} timestamp is later than backup cutoff")

        if invalid:
            warn(f"{label}: {invalid} malformed memory records (missing a nonempty fact)")
        print(f"MEMORY {label}: {facts} nonempty fact records in {len(files)} JSONL files")
        if facts == 0:
            if status.is_file():
                confirm_explicit_empty(status, label, agent_id)
            else:
                warn(f"{label}: zero memory facts; an empty file is not proof of a completed export "
                     "(supply a recent memory-status.json only after checking)")
        elif status.is_file():
            warn(f"{label}: memory-status.json declares no memories but JSONL contains {facts} facts")

    for agent in st["agents"]:
        name = agent["name"]
        if agent.get("kind") == "group":
            group_mem = export_memory_dir(agent)
            if list(group_mem.glob("*.jsonl")) or (group_mem / "memory-status.json").is_file():
                verify_memory_set(group_mem, f"group {name} ({agent['agent_id']})",
                                  agent["agent_id"])
            else:
                print(f"SKIP MEMORY {name}: group chat is not an individual assistant; "
                      "no separate memory drop is required")
            continue
        if agent.get("kind") != "agent":
            continue
        verify_memory_set(export_memory_dir(agent), f"{name} ({agent['agent_id']})",
                          agent["agent_id"])

    # Shared user memory is optional when unavailable, but cannot be silently
    # counted as exported if it is present as a zero-record file.
    shared_memory = (root / "shared" / "platform-memory" if
                     (root / "conversations").exists() else root / "00-shared" / "memory")
    if shared_memory.is_dir() and (list(shared_memory.glob("*.jsonl")) or
                                   (shared_memory / "memory-status.json").is_file()):
        verify_memory_set(shared_memory, "shared user memory")
    else:
        print("SKIP MEMORY shared user memory: no export available; "
              "shared memory may not be accessible to this assistant")

    # Compare copied platform drops to the input set when the live source is
    # still available. Do not use current source mtimes to assess backup age.
    drop = pathlib.Path(os.environ.get(
        "MEMORY_DROP", str(pathlib.Path(os.environ.get("WORKSPACE", "/workspace")) / "exports" / "memory-drop")))
    exported_sha = {sha(p) for p in root.rglob("*.jsonl") if p.is_file()}
    exported_status_sha = {sha(p) for p in root.rglob("memory-status.json") if p.is_file()}
    shared = drop / "shared-user" / "memory-user.jsonl"
    if shared.is_file():
        check(sha(shared) in exported_sha, "shared memory-user.jsonl copied into export")
    shared_status = drop / "shared-user" / "memory-status.json"
    if shared_status.is_file():
        check(sha(shared_status) in exported_status_sha,
              "shared memory-status.json copied into export")
    if (drop / "agents").is_dir():
        for agent_dir in (drop / "agents").iterdir():
            if not agent_dir.is_dir():
                continue
            missing = [p.name for p in agent_dir.glob("*.jsonl")
                       if p.is_file() and sha(p) not in exported_sha]
            check(not missing, f"memory drop copied for {agent_dir.name}"
                  + ("" if not missing else " missing " + ",".join(missing)))
            status_file = agent_dir / "memory-status.json"
            if status_file.is_file():
                check(sha(status_file) in exported_status_sha,
                      f"memory-status.json copied for {agent_dir.name}")

    print("RESULT", "OK" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
