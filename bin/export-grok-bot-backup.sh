#!/usr/bin/env bash
# Export Grok Bot durable data from the shared box computer.
# Includes: all agents' profiles/settings/avatars/memory/routines/attachments/reference,
#           conversation transcripts, user skills (workflows), workspace work products.
#           + full chat history of every assistant from the platform-synced search index
#             (conversations/<display-name>/YYYY-MM.md), + a copy of this program at its git commit.
# NOTE: this is a FULL, SENSITIVE local backup. Included workspace and agent files may contain credentials. Never publish export archives.
# Part of company-notes/grok-bot-backup; run directly.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

AGENT_DATA="${AGENT_DATA:-/home/box/agent-data}"
WORKSPACE="${WORKSPACE:-/workspace}"
OUT_ROOT="${OUT_ROOT:-${WORKSPACE}/exports}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${OUT_ROOT}/grok-bot-export-${STAMP}"
ARCHIVE="${OUT_ROOT}/grok-bot-export-${STAMP}.tar.gz"

# 2026-10-09: private rules removed (owner decision) — every backup is a full backup.
# --include-private / --passphrase are still accepted for compatibility but ignored.
DEPRECATED_FLAG=0

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-private) DEPRECATED_FLAG=1; shift ;;
    --passphrase) DEPRECATED_FLAG=1; shift; if [[ $# -gt 0 ]]; then shift; fi ;;
    --passphrase=*) DEPRECATED_FLAG=1; shift ;;
    -h|--help)
      echo "Usage: $0"
      echo "  Full backup of everything (no private rules since 2026-10-09)."
      echo "  Env: INCLUDE_SUBAGENTS=1 also copies sand-subagent transcripts."
      echo "       INCLUDE_BULKY_DIRS=1 copies .venv*, .cache, node_modules, __pycache__, .git."
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

if [[ "$DEPRECATED_FLAG" -eq 1 ]]; then
  echo "NOTICE: --include-private/--passphrase are deprecated and ignored; every backup now includes everything (2026-10-09)." >&2
fi

[[ -d "$AGENT_DATA" ]] || die "AGENT_DATA not found: $AGENT_DATA"
mkdir -p "$DEST"/{agents,shared,workspace,meta}

# Resolve symlink for clarity in manifest
AGENT_DATA_REAL="$(readlink -f "$AGENT_DATA" 2>/dev/null || echo "$AGENT_DATA")"

copy_tree() {
  local src="$1" dst="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
  fi
}

# --- Per-agent durable state ---
shopt -s nullglob
AGENT_DIRS=("$AGENT_DATA"/agents/*/)
shopt -u nullglob

AGENT_COUNT=0
for agent_dir in "${AGENT_DIRS[@]}"; do
  [[ -d "$agent_dir" ]] || continue
  agent_id="$(basename "$agent_dir")"
  # skip non-uuid folders if any
  [[ -f "${agent_dir}profile.json" || -f "${agent_dir}settings.json" ]] || continue
  AGENT_COUNT=$((AGENT_COUNT + 1))
  out="$DEST/agents/${agent_id}"
  mkdir -p "$out"

  for f in profile.json settings.json group.json projects.json; do
    [[ -f "${agent_dir}${f}" ]] && cp -a "${agent_dir}${f}" "$out/"
  done

  # avatars
  shopt -s nullglob
  for av in "${agent_dir}"avatar.*; do
    cp -a "$av" "$out/"
  done
  shopt -u nullglob

  # memory, routines, channels-ish folders, attachments, reference
  for d in memory automations channels attachments reference; do
    [[ -d "${agent_dir}${d}" ]] && cp -a "${agent_dir}${d}" "$out/"
  done

  # notes: copied in full (no private rules since 2026-10-09)
  [[ -d "${agent_dir}notes" ]] && cp -a "${agent_dir}notes" "$out/"

  # optional: include main sqlite stores (conversation/blob state) without WAL/SHM
  for db in store.db conversation-blobs.db; do
    [[ -f "${agent_dir}${db}" ]] && cp -a "${agent_dir}${db}" "$out/"
  done

  # human-readable audit trail (not secrets)
  [[ -f "${agent_dir}audit.jsonl" ]] && cp -a "${agent_dir}audit.jsonl" "$out/"

  # name for index
  if [[ -f "${agent_dir}profile.json" ]] && command -v python3 >/dev/null; then
    python3 - "$agent_dir" "$out" <<'PY'
import json,sys,pathlib
src=pathlib.Path(sys.argv[1])/"profile.json"
out=pathlib.Path(sys.argv[2])
try:
    data=json.loads(src.read_text(encoding="utf-8"))
    (out/"_export_name.txt").write_text(data.get("name") or "(unnamed)", encoding="utf-8")
except Exception as e:
    (out/"_export_name.txt").write_text(f"(unreadable: {e})", encoding="utf-8")
PY
  fi
done

# --- Shared user memory / projects (may be absent until first write) ---
for d in user-memory projects; do
  if [[ -d "$AGENT_DATA/$d" ]]; then
    cp -a "$AGENT_DATA/$d" "$DEST/shared/$d"
  fi
done

# User-authored skills (workflows). Skip Cursor managed-skills / plugin-skills.
if [[ -d "$AGENT_DATA/workflows" ]]; then
  cp -a "$AGENT_DATA/workflows" "$DEST/shared/workflows"
fi

# Conversation transcripts (jsonl) — useful portable history
if [[ -d "$AGENT_DATA/agent-transcripts" ]]; then
  mkdir -p "$DEST/shared/agent-transcripts"
  # copy agent transcripts; skip sand-subagent noise unless INCLUDE_SUBAGENTS=1
  shopt -s nullglob
  for tdir in "$AGENT_DATA"/agent-transcripts/*/; do
    base="$(basename "$tdir")"
    if [[ "$base" == sand-subagent-* && "${INCLUDE_SUBAGENTS:-0}" != "1" ]]; then
      continue
    fi
    cp -a "$tdir" "$DEST/shared/agent-transcripts/$base"
  done
  shopt -u nullglob
fi

# --- Workspace contents; the only unconditional exclusion is our own export output. ---
python3 -m nuwa_backup.workspace "$WORKSPACE" "$DEST/workspace" "$OUT_ROOT" "$DEST/meta/workspace-copy.json"

# --- Full conversations of every assistant (platform-synced search index) ---
python3 -m nuwa_backup.conversations --dest "$DEST/conversations" >/dev/null

# --- Platform memory drops (phase 1). Missing agents are not exported, never fatal. ---
python3 -m nuwa_backup.memory_drop --into-grok "$DEST" >/dev/null

# --- Copy of this backup program: repo source at HEAD + commit hash ---
python3 -m nuwa_backup.program "$DEST/meta/backup-program" > "$DEST/meta/.program.out"
PROGRAM_COMMIT="$(sed -n 's/^PROGRAM_COMMIT=//p' "$DEST/meta/.program.out")"
rm -f "$DEST/meta/.program.out"

# --- Manifest ---
python3 - "$DEST" "$AGENT_DATA" "$AGENT_DATA_REAL" "$STAMP" "$AGENT_COUNT" "$PROGRAM_COMMIT" <<'PY'
import json, os, sys, hashlib, pathlib
from datetime import datetime, timezone

dest = pathlib.Path(sys.argv[1])
agent_data = sys.argv[2]
agent_data_real = sys.argv[3]
stamp = sys.argv[4]
agent_count = int(sys.argv[5])
program_commit = sys.argv[6]
conv = json.loads((dest / "conversations" / "conversation-stats.json").read_text(encoding="utf-8"))
conv_total = sum(a["messages"] for a in conv["agents"])
workspace_report = json.loads((dest / "meta" / "workspace-copy.json").read_text(encoding="utf-8"))

def file_list(root: pathlib.Path):
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root))
            st = p.stat()
            rows.append({"path": rel, "bytes": st.st_size})
    return rows

files = file_list(dest)
total_bytes = sum(f["bytes"] for f in files)

agents = []
agents_dir = dest / "agents"
if agents_dir.is_dir():
    for d in sorted(agents_dir.iterdir()):
        if not d.is_dir():
            continue
        name = None
        np = d / "_export_name.txt"
        if np.exists():
            name = np.read_text(encoding="utf-8").strip()
        agents.append({"id": d.name, "name": name})

manifest = {
    "format": "grok-bot-box-export-v2",
    "program_commit": program_commit,
    "conversations": {"source": conv["source"], "cutoff_taipei": conv["cutoff_taipei"],
                      "total_messages": conv_total,
                      "per_agent": {a["name"]: a["messages"] for a in conv["agents"]}},
    "exported_at_utc": datetime.now(timezone.utc).isoformat(),
    "stamp": stamp,
    "agent_data_path": agent_data,
    "agent_data_realpath": agent_data_real,
    "agent_count": agent_count,
    "agents": agents,
    "included": [
        "agents/*/profile.json, settings.json, avatar.*, memory/, automations/",
        "agents/*/attachments/, reference/, audit.jsonl, notes/ (all)",
        "agents/*/store.db, conversation-blobs.db (no WAL/SHM)",
        "shared/workflows (user skills)",
        "shared/user-memory, shared/projects (if present)",
        "shared/agent-transcripts (excluding sand-subagent-* unless INCLUDE_SUBAGENTS=1)",
        "workspace/ (excludes only backup output and, by default, bulky directories; see meta/workspace-copy.json)",
        "conversations/<name>/YYYY-MM.md full chat history from search-index.db (+ raw/, attachment-index.md, frozen supplement)",
        "meta/backup-program/ (repo source at HEAD + COMMIT)",
        "platform-memory/ human-readable RecallMemory drops (00-shared/memory, 01-assistants/<name>/memory) when /workspace/exports/memory-drop exists",
        "agents/<id>/platform-memory/ and shared/platform-memory/ raw jsonl from those drops",
    ],
    "excluded": [
        "Host-level files outside the included trees are not deliberately copied; included folders can still contain secrets",
        "managed-skills/, plugin-skills/ (Cursor-managed)",
        "*.db-wal, *.db-shm, locks, telemetry, search-index*, sand-statsig*",
        "sand-subagent transcripts (default)",
    ],
    "workspace_copy": workspace_report,
    "include_subagents": os.environ.get("INCLUDE_SUBAGENTS", "0") == "1",
    "full_backup": not any(x.endswith("(bulky directory)") for x in workspace_report["skipped"]) and os.environ.get("INCLUDE_SUBAGENTS", "0") == "1",
    "full_backup_note": "This flag describes optional workspace/subagent exclusions only; platform memory or inaccessible sources may remain missing.",
    "notes": [
        "All assistants on one account share this computer filesystem.",
        "Per-agent identity/memory lives under agents/<id>/; skills under workflows/; scratch deliverables under /workspace.",
        "This archive is a portable backup for leaving Grok Bot lock-in; restore is manual copy-back / re-import.",
        "Full backup: no private rules since 2026-10-09; all notes, attachments and conversations are included.",
        "Local memory/ files are still frozen at 2026-10-06 15:10.",
        "Platform memory phase 1: copied from /workspace/exports/memory-drop when present (see platform-memory/memory-index.md). Agents without a drop are marked not exported.",
    ],
    "file_count": len(files),
    "total_bytes": total_bytes,
    "files": files,
}

(dest / "meta" / "MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

readme = f"""# Grok Bot box export ({stamp})

Exported from shared box computer.

## What's inside
- `agents/<agent-id>/` — profile, settings, avatar, memory, routines, attachments, reference, sqlite stores
- `shared/workflows/` — user-authored skills
- `conversations/` — **full chat history of every assistant** (one folder per display name, monthly Markdown, Asia/Taipei); start at `conversations/README.md`
- `shared/agent-transcripts/` — frozen local transcript jsonl with tool calls (until ~2026-09-16)
- `shared/user-memory/`, `shared/projects/` — only if those folders exist (local files, frozen 2026-10-06)
- `platform-memory/` — human-readable platform memory from RecallMemory drops (phase 1); index at `platform-memory/memory-index.md`
- `agents/<id>/platform-memory/` — raw jsonl of that assistant's drop, if any
- `workspace/` — shared work products / deliverables from `/workspace`
- `meta/MANIFEST.json` — file inventory
- `meta/backup-program/` — the backup program used (repo source at commit `{program_commit}`)

## Excluded on purpose
Host files outside selected trees, SQLite WAL/SHM, and (by default) sand-subagent transcripts. Bulky workspace directories are optional; inspect `meta/workspace-copy.json` for exact exclusions.

## Re-run
```bash
bin/export-grok-bot-backup.sh
# or:
INCLUDE_SUBAGENTS=1 INCLUDE_BULKY_DIRS=1 bin/export-grok-bot-backup.sh
```

Conversation messages: {conv_total} (snapshot {conv["cutoff_taipei"]} Asia/Taipei)
Platform memory (phase 1): see `platform-memory/memory-index.md`. Local `memory/` files remain frozen at 2026-10-06.

Agents found: {agent_count}
Total files: {len(files)}
Total bytes: {total_bytes}
"""
(dest / "README.md").write_text(readme, encoding="utf-8")
print(f"Manifest written: {len(files)} files, {total_bytes} bytes, {agent_count} agents")
PY

# --- Tar ---
mkdir -p "$OUT_ROOT"
find "$DEST" -type d -exec chmod 700 {} + ; find "$DEST" -type f -exec chmod 600 {} +
tar -C "$OUT_ROOT" -czf "$ARCHIVE" "$(basename "$DEST")"
chmod 600 "$ARCHIVE"

# summary
python3 - "$DEST" "$ARCHIVE" <<'PY'
import json, pathlib, sys
dest=pathlib.Path(sys.argv[1])
archive=pathlib.Path(sys.argv[2])
m=json.loads((dest/"meta"/"MANIFEST.json").read_text(encoding="utf-8"))
print("EXPORT_DIR="+str(dest))
print("ARCHIVE="+str(archive))
print("ARCHIVE_BYTES="+str(archive.stat().st_size))
print("FILE_COUNT="+str(m["file_count"]))
print("TOTAL_BYTES="+str(m["total_bytes"]))
print("AGENT_COUNT="+str(m["agent_count"]))
print("PROGRAM_COMMIT="+str(m["program_commit"]))
print("CONVERSATION_MESSAGES="+str(m["conversations"]["total_messages"]))
for a in m["agents"]:
    print(f"AGENT\t{a['id']}\t{a.get('name')}")
PY
