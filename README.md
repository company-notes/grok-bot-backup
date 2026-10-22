# Grok Bot Backup

Unofficial, open-source export utilities for **Grok Bot** shared-box environments. Keep as much accessible data as possible in two forms: raw files and human-readable Markdown for handover to another AI assistant or a person.

**Status:** The previous Virtual Office implementation had a severe recursive-copy bug that filled a shared box disk. This revision includes regression tests and checks both output locations. **Run a fresh-box validation before relying on it in production.** This project is not a one-click restore tool.

## Quick start

Requirements: Linux, Bash, Python 3.12+, Git, tar, and **filesystem access** to your Grok Bot shared box (`/home/box/agent-data` by default). It does not log into a web account or download your data from the cloud.

```bash
git clone https://github.com/company-notes/grok-bot-backup.git
cd grok-bot-backup
cp config.example.json config.json  # Optional: edit your assistant roster

python3 -m unittest discover -s tests -v
bin/export-grok-bot-backup.sh         # Raw data + portable conversations
bin/export-virtual-office.sh         # Human-readable Markdown handover
```

Backups are created under `OUT_ROOT` (default: `/workspace/exports`). Each command prints its output paths. **Neither command encrypts its results.** All accessible content, including passwords, private files, attachments, and credentials, is included by default. Restrictive Unix permissions do not replace encryption. Store the resulting archives wherever your own policy requires, and never commit real exports to a public repository.

**Disk-space planning:** there is no fixed minimum. Size depends on your source data. Allow room for the uncompressed export directory **and** its compressed archive, plus headroom for other agents using the box. If the disk is limited, consider excluding large regenerable directories or choosing an external `OUT_ROOT`.

## Selective exclusions: full content by default

Both export commands use the same safe workspace copier. They **always** exclude the currently configured `OUT_ROOT` and `$WORKSPACE/exports` (even if `OUT_ROOT` points elsewhere). This avoids recursively backing up newly created and older exports.

Everything else under the accessible workspace, **including `secrets/`**, is eligible for backup unless excluded explicitly. There is no sensitive-content filter. Agents' `private/` folders are also included.

```bash
# Include any secrets by default:
bin/export-grok-bot-backup.sh

# Optional: skip a particular workspace directory:
bin/export-grok-bot-backup.sh --exclude secrets
bin/export-virtual-office.sh --exclude secrets

# Multiple workspace-relative or absolute paths (Linux path separator ':'):
BACKUP_EXCLUDE_PATHS="secrets:old-archives:/mnt/other-data" bin/export-grok-bot-backup.sh

# Or repeat the command-line option:
bin/export-virtual-office.sh --exclude secrets --exclude old-archives
```

The `BACKUP_EXCLUDE_PATHS` variable and `--exclude` options apply to **workspace paths**. They do not automatically filter agent memory, conversations, or private content in `AGENT_DATA`. Exclusions are recorded in the raw export's `meta/workspace-copy.json` and the Virtual Office export's `workspace-copy.json`.

Large build/caching directories (`.venv*`, `.cache`, `node_modules`, `__pycache__`, `.git`) are skipped by default to control size; use `INCLUDE_BULKY_DIRS=1` to include them. To include the optional sand-subagent transcripts in the raw export, use `INCLUDE_SUBAGENTS=1`:

```bash
INCLUDE_BULKY_DIRS=1 INCLUDE_SUBAGENTS=1 bin/export-grok-bot-backup.sh
INCLUDE_BULKY_DIRS=1 bin/export-virtual-office.sh
```

**Warning:** "All accessible data" cannot include information the local platform does not expose. See memory preparation below.

## Prepare platform memory *before* backup

The program can copy local memory files, but it **cannot invoke each assistant's internal RecallMemory tool**. An assistant must explicitly export its own currently available memory first. Do this for **every assistant** on the shared box and for shared (`scope=user`) memory where permitted.

Set `MEMORY_DROP` to the prepared directory. Default: `$WORKSPACE/exports/memory-drop`. Use this structure:

```text
memory-drop/
├── shared-user/
│   └── memory-user.jsonl
└── agents/
    └── <agent-uuid>/
        ├── memory-profile.jsonl
        └── memory-agent.jsonl
```

The program accepts any `*.jsonl` under `agents/<agent-uuid>/`. Each line must be one JSON object, preserving the **full original memory text**, for example:

```json
{"date":"2026-10-22","tier":"profile","scope":"agent","source":"RecallMemory","fact":"Example persistent fact"}
```

Use `tier` of `profile`, `log`, or `note`, with `scope` and `source` reflecting what the assistant actually retrieved. Do not invent facts or claim completeness if recall was partial. The exporter also preserves the JSONL files byte-for-byte alongside readable Markdown.

Copy this request to **each** assistant (adjust for its tool access):

> Use your available RecallMemory interface to retrieve all memory facts you are permitted to see, covering every available tier and scope, including older records. Export the complete facts—not summaries—to valid UTF-8 JSONL, one JSON object per line, under `$MEMORY_DROP/agents/<your-agent-uuid>/`. Include `date`, `tier`, `scope`, `source`, and `fact`. If you can export shared user memory, write it to `$MEMORY_DROP/shared-user/memory-user.jsonl`. Report any tool limitations and the number of facts exported. Do not modify the source memories.

**Freshness matters.** The verifier uses the JSONL files' modification times as the export-time proxy; the raw files are copied with their timestamps preserved. An old `memory-drop` can make a backup look complete even though new memories are missing. Do not reset timestamps to pretend old exports are new.

## Verify the backup

```bash
bin/verify-export.sh /workspace/exports/grok-bot-export-<UTC>
bin/verify-export.sh /workspace/exports/virtual-office-<UTC>

# Default max memory age is 2 days; stale/missing drops show WARN.
MEMORY_MAX_AGE_DAYS=2 bin/verify-export.sh /path/to/export

# Require memory drops to be present and recent:
MEMORY_MAX_AGE_DAYS=2 bin/verify-export.sh /path/to/export --strict

# Optional: require at least one conversation on a particular day:
bin/verify-export.sh /path/to/export --date YYYY-MM-DD
```

The verifier prints each available assistant's memory-file modification timestamp and age relative to the backup snapshot. It lists missing drops by assistant name. A missing/stale drop is a **WARN** (exit 0 if other checks pass); with `--strict` it becomes **FAIL** (nonzero exit). This check does not prove that all memories have been retrieved—it detects absent or aging export files.

Inspect the raw export's `meta/MANIFEST.json`, `meta/workspace-copy.json`, and `platform-memory/memory-index.md` for included data, exclusions, and RecallMemory status. The readable export has `memory-index.md`, `conversation-index.md`, and `workspace-copy.json`. Verification compares conversation messages against the live search index and checks exported notes and memory drops; check the source before assuming universal compatibility.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_DATA` | `/home/box/agent-data` | Root of the local platform agent data |
| `WORKSPACE` | `/workspace` | Shared workspace |
| `OUT_ROOT` | `$WORKSPACE/exports` | Backup destination; automatically excluded |
| `BACKUP_EXCLUDE_PATHS` | empty | Optional colon-separated workspace paths to omit |
| `INCLUDE_BULKY_DIRS` | `0` | Set `1` to include large dependency/cache folders |
| `INCLUDE_SUBAGENTS` | `0` | Set `1` to include sand-subagent transcripts in raw backup |
| `MEMORY_DROP` | `$WORKSPACE/exports/memory-drop` | Pre-exported platform memory files |
| `MEMORY_MAX_AGE_DAYS` | `2` | Max age of a memory drop before WARN / strict FAIL |
| `GROK_BACKUP_CONFIG` | `<repo>/config.json` | Optional expected agent roster |
| `GROK_BACKUP_USER_LABEL` | `User` | Conversation label for the human side |

Older `NUWA_BACKUP_CONFIG` and `NUWA_BACKUP_USER_LABEL` environment variables remain accepted for compatibility, but new setup should use the `GROK_BACKUP_*` names.

## Archive contents

### Raw export: `grok-bot-export-<UTC>/`

- `agents/<uuid>/`: all accessible files belonging to genuine agent UUID directories, including `private/`, notes, settings, attachments, memory, and local databases. Unrelated `organization/` and `people/` folders are not counted as agents.
- `shared/`: local shared memory, projects, workflows, and optionally frozen subagent transcripts.
- `workspace/`: shared workspace files, subject to configurable exclusions.
- `conversations/`: monthly human-readable Markdown and raw JSONL from the platform-synchronized SQLite index.
- `platform-memory/`: readable RecallMemory export, when supplied.
- `meta/`: file inventory, workspace omissions, and this program's git revision.

### Human-readable export: `virtual-office-<UTC>/`

- `00-shared/`: available projects, shared memory, skills, **workspace outputs**.
- `01-assistants/<name>/`: profiles, notes, **private** content, routines, attachments, conversations, memory.
- `02-groups/<name>/`: group information and conversations.
- `99-backup-program/`: source code version and conversation statistics.
- `memory-index.md`, `conversation-index.md`, `workspace-copy.json`: export indexes.

Textual labels and directory names generated by this utility are in English. Original data and messages are **not translated**.

## Limitations

- The platform search index generally provides visible conversation text. Tool calls and tool results can require older on-box transcripts; availability varies by environment.
- Agent local SQLite copies may be live files. The conversation index is snapshot consistently, but **not every raw database is guaranteed to be a consistent snapshot**.
- Local memory can be stale. The memory-drop process requires agent cooperation; exported-fact completeness is not technically guaranteed.
- Different Grok Bot versions may use different internal storage layouts. Paths must be checked on the target box.
- This is an **export**, not an automatic restore workflow. Recovery/rehydration into a new provider must be planned separately.

## Test

```bash
python3 -m compileall -q grok_bot_backup tests
bash -n bin/*.sh
python3 -m unittest discover -s tests -v
```

See `.github/workflows/ci.yml` for continuous integration, and [LICENSE](LICENSE) for the MIT license.
