"""Verify a finished export: conversations vs search index, program copy, notes completeness.

usage: python3 -m nuwa_backup.verify <export-dir> [--archive X.tar.gz] [--date YYYY-MM-DD]
Prints a short report; exit code 1 if any check fails. Never prints message bodies.
"""
from __future__ import annotations

import argparse
import json
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
    a = ap.parse_args()
    root = pathlib.Path(a.export_dir)
    ok = True

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
    check(prog is not None and (prog / "source" / "nuwa_backup" / "common.py").exists(), "program source present")

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
    # platform memory phase 1: if a drop exists, the export must reflect it (or say not exported)
    mem_index = next((p for p in [
        root / "memory-index.md",
        root / "platform-memory" / "memory-index.md",
    ] if p.exists()), None)
    if mem_index is None:
        check(False, "memory-index.md present (phase 1)")
    else:
        idx = mem_index.read_text(encoding="utf-8")
        check("not exported" in idx or "exported" in idx, "memory index mentions exported or not exported")
        # shared jsonl, if present in the drop, must be copied
        import hashlib as _hashlib
        drop = pathlib.Path(__import__("os").environ.get("MEMORY_DROP", "/workspace/exports/memory-drop"))

        def sha(path: pathlib.Path) -> str:
            return _hashlib.sha256(path.read_bytes()).hexdigest()

        exported_sha = {sha(p) for p in root.rglob("*.jsonl") if p.is_file()}
        shared = drop / "shared-user" / "memory-user.jsonl"
        if shared.is_file():
            check(sha(shared) in exported_sha, "shared memory-user.jsonl copied into export")
        agent_dirs = sorted((drop / "agents").glob("*")) if (drop / "agents").is_dir() else []
        for agent_dir in agent_dirs:
            blobs = [p for p in agent_dir.glob("*.jsonl") if p.is_file()]
            if not blobs:
                continue
            missing = [p.name for p in blobs if sha(p) not in exported_sha]
            check(not missing, f"memory drop copied for {agent_dir.name[:8]}"
                  + ("" if not missing else " missing " + ",".join(missing)))

    print("RESULT", "OK" if ok else "FAILED")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
