"""Verify a finished export: conversations vs search index, program copy, notes completeness.

usage: python3 -m grok_bot_backup.verify <export-dir> [--archive X.tar.gz] [--date YYYY-MM-DD]
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
from datetime import datetime

import hashlib

from .common import AGENT_DATA, SEARCH_INDEX, TAIPEI

ENTRY_RE = re.compile(r"^<!-- entry:(\S+) seq:(\S+) -->$", re.M)
HDR_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} · ", re.M)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export_dir")
    ap.add_argument("--archive")
    ap.add_argument("--date", help="Optional day to require at least one message (YYYY-MM-DD)")
    ap.add_argument("--strict", action="store_true",
                    help="Treat missing or stale memory drops as verification failures")
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

    if a.archive:
        with tarfile.open(a.archive) as tar:
            names = tar.getnames()
        check(any(n.endswith("/COMMIT") for n in names), f"archive contains program COMMIT ({len(names)} members)")

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

    def memory_files(agent):
        if (root / "conversations").exists():
            return sorted((root / "agents" / agent["agent_id"] / "platform-memory").glob("*.jsonl"))
        conv_dir = root / agent["conv_dir"]
        return sorted((conv_dir.parent / "memory").glob("*.jsonl"))

    cutoff_seconds = cutoff / 1000
    for agent in st["agents"]:
        if agent.get("kind") != "agent":
            continue
        name = agent["name"]
        files = memory_files(agent)
        if not files:
            warn(f"{name} ({agent['agent_id']}): no platform memory drop found")
            continue
        ages = []
        for mem_file in files:
            modified = mem_file.stat().st_mtime
            age = (cutoff_seconds - modified) / 86400
            ages.append(age)
            stamp = datetime.fromtimestamp(modified, timezone.utc).isoformat()
            print(f"MEMORY {name}: {mem_file.name}, exported/modified {stamp}, "
                  f"age at backup {age:.2f} days")
        if any(age > max_age_days for age in ages):
            warn(f"{name}: memory drop older than {max_age_days:g} days "
                 f"(oldest {max(ages):.2f} days)")
        if any(age < -0.01 for age in ages):
            warn(f"{name}: memory drop timestamp is later than backup cutoff")

    # Compare copied platform drops to the input set when the live source is
    # still available. Do not use current source mtimes to assess backup age.
    drop = pathlib.Path(os.environ.get(
        "MEMORY_DROP", str(pathlib.Path(os.environ.get("WORKSPACE", "/workspace")) / "exports" / "memory-drop")))
    exported_sha = {sha(p) for p in root.rglob("*.jsonl") if p.is_file()}
    shared = drop / "shared-user" / "memory-user.jsonl"
    if shared.is_file():
        check(sha(shared) in exported_sha, "shared memory-user.jsonl copied into export")
    if (drop / "agents").is_dir():
        for agent_dir in (drop / "agents").iterdir():
            if not agent_dir.is_dir():
                continue
            missing = [p.name for p in agent_dir.glob("*.jsonl")
                       if p.is_file() and sha(p) not in exported_sha]
            check(not missing, f"memory drop copied for {agent_dir.name}"
                  + ("" if not missing else " missing " + ",".join(missing)))

    print("RESULT", "OK" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
