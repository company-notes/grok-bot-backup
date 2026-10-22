#!/usr/bin/env python3
"""Export Grok Bot durable state as a human-readable Virtual Office pack.

Not for restoring Grok internals. Organized for people / any virtual office:
  00-shared/   01-assistants/<display-name>/   02-groups/<group-name>/   99-backup-program/
Each assistant gets conversations/ with the full chat history (monthly Markdown)
from the platform-synced search index.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import tarfile

from . import conversations
from .memory_drop import apply_tree as apply_memory_drop
from .common import (
    AGENT_DATA, OUT_ROOT, WORKSPACE, add_deprecated_private_args, copy_if,
    lock_down, safe_name, utc_stamp, warn_deprecated_private_args, write, valid_agent_id,
)
from .program import snapshot_program
from .workspace import copy_workspace

STAMP = utc_stamp()
DEST = OUT_ROOT / f"virtual-office-{STAMP}"


def export_agent(agent_dir: pathlib.Path, dest_agents: pathlib.Path, id_to_name: dict) -> str | None:
    profile_path = agent_dir / "profile.json"
    if not profile_path.exists() or (agent_dir / "group.json").exists():
        return None
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    name = profile.get("name") or agent_dir.name
    if name.strip() == "New Bot" and not (profile.get("description") or "").strip():
        name = "New Bot (not configured)"
    folder = dest_agents / safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)

    desc = profile.get("description") or ""
    title = profile.get("title") or ""
    settings = {}
    sp = agent_dir / "settings.json"
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    write(folder / "profile.md", f"""# {name}

- Internal ID: `{agent_dir.name}`
- Title: {title or "(none)"}
- Export time (UTC): {STAMP}

## Role description

{desc.strip() or "(no description)"}

## Settings summary

```json
{json.dumps(settings, ensure_ascii=False, indent=2)}
```
""")

    for av in agent_dir.glob("avatar.*"):
        copy_if(av, folder / f"avatar{av.suffix.lower()}")

    # memory (local files; frozen since 2026-10-06 — see README)
    mem = agent_dir / "memory"
    if mem.exists():
        for p in mem.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "memory" / p.relative_to(mem))
        parts = ["# Memory summary\n", "> Local memory files may be incomplete or outdated; consult memory-index.md for platform memory drops.\n"]
        for p in sorted(mem.rglob("*.md")):
            parts.append(f"## {p.relative_to(mem)}\n\n{p.read_text(encoding='utf-8', errors='replace')}\n")
        write(folder / "memory-summary.md", "\n".join(parts))

    auto = agent_dir / "automations"
    if auto.exists() and any(auto.iterdir()):
        for p in auto.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "routines" / p.relative_to(auto))

    for src_name, dst_name in [("attachments", "attachments-and-work"), ("reference", "references"), ("private", "private")]:
        src = agent_dir / src_name
        if src.exists():
            for p in src.rglob("*"):
                if p.is_file():
                    copy_if(p, folder / dst_name / p.relative_to(src))

    # notes: copy all files in their source structure (no private-content filtering since 2026-10-09)
    notes = agent_dir / "notes"
    if notes.exists():
        for p in notes.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "notes-and-profiles" / p.relative_to(notes))

    write(folder / "README.md", f"""# {name}

This is a portable virtual employee/role package that a person can read or import into another virtual office.

| File / folder | Description |
|---|---|
| `profile.md` | Name, role, and behavior guidelines |
| `avatar.*` | Avatar image |
| `conversations/` | **Full conversations** (one Markdown file per month, Taipei time; see README.md) |
| `memory/`, `memory-summary.md` | Long-term memory (local files frozen since 2026-10-06) |
| `notes-and-profiles/` | All assistant notes, preserving their directory structure |
| `attachments-and-work/` | Chat attachments and outputs (hash-based filenames; see `conversations/attachment-index.md`) |
| `references/` | Role reference documents |
| `routines/` | Scheduled routines (if present) |
""")
    id_to_name[agent_dir.name] = name
    return name


def export_group(group_dir: pathlib.Path, dest_groups: pathlib.Path, id_to_name: dict) -> None:
    profile = json.loads((group_dir / "profile.json").read_text(encoding="utf-8"))
    group = json.loads((group_dir / "group.json").read_text(encoding="utf-8"))
    name = profile.get("name") or group_dir.name
    folder = dest_groups / safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    members = group.get("memberIds") or []
    member_lines = [f"- {id_to_name.get(mid, mid)} (`{mid}`)" for mid in members]
    write(folder / "group-profile.md", f"""# Group: {name}

- Internal ID: `{group_dir.name}`
- Export time (UTC): {STAMP}

## Members

{chr(10).join(member_lines) or "(none)"}

## Description

{profile.get("description") or "(no additional description)"}
""")
    mem = group_dir / "memory"
    if mem.exists():
        for p in mem.rglob("*"):
            copy_if(p, folder / "memory" / p.relative_to(mem))
    write(folder / "README.md", f"""# Group: {name}

Human-readable group package. See group-profile.md for members and conversations/ for group chat (if available).
""")


def export_shared(dest_shared: pathlib.Path) -> dict:
    """Copy shared data, including the workspace without recursive export paths."""
    dest_shared.mkdir(parents=True, exist_ok=True)
    project_root = AGENT_DATA / "projects"
    if project_root.is_dir():
        for project in project_root.iterdir():
            if project.is_dir():
                shutil.copytree(project, dest_shared / "projects" / project.name,
                                symlinks=True, dirs_exist_ok=True)
    for src_name, dst_name in [("user-memory", "shared-agent-memory"),
                               ("workflows", "skills-and-workflows")]:
        src = AGENT_DATA / src_name
        if src.is_dir():
            shutil.copytree(src, dest_shared / dst_name,
                            symlinks=True, dirs_exist_ok=True)

    # Same safe copier as raw export: never recursively copy export directories.
    report = copy_workspace(WORKSPACE, dest_shared / "workspace-outputs", OUT_ROOT)
    write(dest_shared / "README.md", """# Shared area

| Folder | Description |
|---|---|
| `projects/` | All available platform projects |
| `shared-agent-memory/` | Locally stored shared memories |
| `skills-and-workflows/` | User-defined workflows and skills |
| `workspace-outputs/` | Workspace files subject to the documented exclusion policy |
""")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Virtual Office readable pack")
    add_deprecated_private_args(parser)
    args = parser.parse_args()
    warn_deprecated_private_args(args)

    os.umask(0o077)
    print("WARNING: Unencrypted full-data backup; secrets, credentials, and private files may be included.", flush=True)
    print(f"WORKSPACE={WORKSPACE}; OUT_ROOT={OUT_ROOT}; BACKUP_EXCLUDE_PATHS={os.environ.get('BACKUP_EXCLUDE_PATHS', '')}", flush=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    id_to_name: dict[str, str] = {}
    agents_root = AGENT_DATA / "agents"
    dest_agents, dest_groups, dest_shared = DEST / "01-assistants", DEST / "02-groups", DEST / "00-shared"

    for d in sorted(agents_root.iterdir()):
        if d.is_dir() and valid_agent_id(d.name) and (d / "profile.json").exists() and not (d / "group.json").exists():
            export_agent(d, dest_agents, id_to_name)
    for d in sorted(agents_root.iterdir()):
        if d.is_dir() and valid_agent_id(d.name) and (d / "group.json").exists():
            export_group(d, dest_groups, id_to_name)
    workspace_report = export_shared(dest_shared)
    write(DEST / 'workspace-copy.json', json.dumps(workspace_report, ensure_ascii=False, indent=2))
    mem_status = apply_memory_drop(DEST, id_to_name)

    # ---- full conversations from the platform-synced search index
    def layout(info):
        base = dest_groups if info["kind"] == "group" else dest_agents
        return base / info["folder"] / "conversations"

    def attach_link_for(info, conv_dir):
        if info.get("dir") is None or info["kind"] == "group":
            return None
        return lambda f: f"../attachments-and-work/{f}"

    conv_stats = conversations.export_all(
        layout, attach_link_for, DEST / "conversation-index.md",
        lambda info, d: str(d.relative_to(DEST)), DEST / "99-backup-program" / "conversation-stats.json",
        rel_root=DEST,
    )
    # ---- copy of this program (repo at HEAD + commit hash)
    prog = snapshot_program(DEST / "99-backup-program")

    roster = ["# Roster"] + [f"- {n} (`{i}`)" for i, n in sorted(id_to_name.items(), key=lambda x: x[1])]
    write(DEST / "roster.md", "\n".join(roster))

    total_msgs = sum(s["messages"] for s in conv_stats)
    write(DEST / "README.md", f"""# Human-readable Virtual Office export

- Export time (UTC): `{STAMP}`
- Purpose: readable by people and portable to another virtual office; not an internal Grok Bot restore image
- The readable package is not byte-for-byte equivalent to the original data; inspect grok-bot-export for raw state.
- Full backup: all available notes, attachments and conversations are included (no private-content filtering since 2026-10-09).
- Conversations: {len([s for s in conv_stats if s['messages']])} assistants/groups, totaling {total_msgs} messages (platform-synced search-index.db, including messages after 2026-10-06)
- Backup program version: commit `{prog.get('commit')}` (full source in 99-backup-program/source/)

## Contents

- conversation-index.md — conversation overview for all assistants (counts, ranges, links)
- `00-shared/` — Company knowledge, shared memory, skills and deliverables
- 01-assistants/<name>/ — profiles, avatars, memory, monthly conversation Markdown, notes and attachments
- `02-groups/<name>/` — group metadata and conversations
- `99-backup-program/` — source code, commit and conversation statistics for this backup
- `roster.md`

## Search

After extraction: `grep -rn "keyword" 01-assistants/*/conversations/`

## Known limitations

- Memory phase 1: available drops from /workspace/exports/memory-drop/ are written to 00-shared/memory/ and the assistant memory/platform-memory.md files (see memory-index.md). Missing drops are labeled 'not exported'. Local memory files are frozen as of 2026-10-06 15:10.
- The platform index contains only visible chat text; tool calls and results are in the frozen local transcript supplement (until around 2026-09-16).
- For group conversations, the index does not identify which assistant spoke.
""")

    lock_down(DEST)
    archive = OUT_ROOT / f"virtual-office-{STAMP}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(DEST, arcname=DEST.name)
    archive.chmod(0o600)

    files = [p for p in DEST.rglob("*") if p.is_file()]
    print(f"DEST={DEST}")
    print(f"ARCHIVE={archive}")
    print(f"FILES={len(files)}")
    print(f"BYTES={sum(p.stat().st_size for p in files)}")
    print(f"ARCHIVE_BYTES={archive.stat().st_size}")
    print(f"PROGRAM_COMMIT={prog.get('commit')} DIRTY={prog.get('dirty')}")
    print(f"CONVERSATION_MESSAGES={total_msgs}")
    su = mem_status.get("shared_user")
    print("MEMORY_SHARED=" + ("MISSING" if su is None else str(su["count"])))
    for a in mem_status["agents"]:
        flag = "yes" if a["dropped"] else "not exported"
        print(f"MEMORY_AGENT\t{flag}\t{a['id']}\t{a['name']}")
    for name in sorted(id_to_name.values()):
        print(f"AGENT\t{name}")


if __name__ == "__main__":
    main()
