# Grok Bot Backup

**Unofficial, open-source backup and export tools for Grok Bot assistants.** Export accessible data from a Grok Bot shared box as both a raw archive and a human-readable Virtual Office package, including assistant profiles, memories, notes, attachments, workspace deliverables, and chat history.

This is a backup/export utility, **not an official Grok Bot API or a one-click restore tool**. It expects filesystem access to the platform's shared box, including `agent-data/search-index.db`. Platform storage formats and permissions can change.

## Requirements

- Linux box with Bash, Python 3, `git`, `tar`, and the Grok Bot shared data directories available.
- Run from a **cloned Git checkout**. The export includes `git archive HEAD` and a commit ID to identify the version used.
- Permission to read the underlying agent data and write to the export directory.

## Quick start

```bash
git clone https://github.com/company-notes/grok-bot-backup.git
cd grok-bot-backup

cp config.example.json config.json
# Edit config.json if you want to track expected memory drops.

python3 -m unittest discover -s tests -v
bin/export-virtual-office.sh          # Human-readable archive
bin/export-grok-bot-backup.sh         # Raw export + portable conversation records
```

Both commands print the destination and archive path. To verify an export:

```bash
bin/verify-export.sh /workspace/exports/virtual-office-YYYYMMDDTHHMMSSZ
bin/verify-export.sh /workspace/exports/grok-bot-export-YYYYMMDDTHHMMSSZ

# Optional: require at least one message on a specific date.
bin/verify-export.sh /path/to/export --date YYYY-MM-DD
```

The default verification checks conversation counts against the platform index, checks program provenance, and checks the notes and available memory drops. It is **not a guarantee that inaccessible platform state can be recovered**.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_DATA` | `/home/box/agent-data` | Platform agent data root |
| `WORKSPACE` | `/workspace` | Shared workspace |
| `OUT_ROOT` | `$WORKSPACE/exports` | Directory for exports |
| `MEMORY_DROP` | `$WORKSPACE/exports/memory-drop` | RecallMemory drop location |
| `NUWA_BACKUP_CONFIG` | `<repo>/config.json` | Optional list of expected agents |
| `NUWA_BACKUP_USER_LABEL` | `User` | Label for the user side of chat history |
| `INCLUDE_SUBAGENTS` | `0` | Set `1` to include sand-subagent transcript directories |
| `INCLUDE_BULKY_DIRS` | `0` | Set `1` to include large workspace directories such as `.venv*`, `.cache`, `node_modules`, `__pycache__`, and `.git` |

For the most inclusive backup supported by this code:

```bash
INCLUDE_BULKY_DIRS=1 INCLUDE_SUBAGENTS=1 bin/export-grok-bot-backup.sh
INCLUDE_BULKY_DIRS=1 bin/export-virtual-office.sh
```

The tool excludes its own output to prevent recursive archiving. With `INCLUDE_BULKY_DIRS=0`, skipped items are recorded in the raw export's `meta/workspace-copy.json`. Even with both options enabled, some source data may be inaccessible or unsynchronized.

## What gets exported

### Human-readable Virtual Office package

The `virtual-office-<UTC>/` directory uses **English folder names and English-generated headings**, but **never translates or rewrites the original conversations, notes, or memories**:

```text
virtual-office-<UTC>/
├── README.md
├── conversation-index.md
├── roster.md
├── memory-index.md
├── 00-shared/
│   ├── company/
│   ├── shared-agent-memory/
│   ├── skills-and-workflows/
│   ├── workspace-outputs/
│   └── memory/               # Shared RecallMemory exports, when supplied
├── 01-assistants/<display-name>/
│   ├── profile.md
│   ├── conversations/         # YYYY-MM.md, README.md, raw JSONL, attachment-index.md
│   ├── memory/
│   ├── memory-summary.md
│   ├── notes-and-profiles/
│   ├── attachments-and-work/
│   ├── references/
│   └── routines/
├── 02-groups/<group-name>/
└── 99-backup-program/         # Source snapshot, commit, version, conversation stats
```

### Raw Grok Bot export

The `grok-bot-export-<UTC>/` directory includes, where available:

- `agents/<id>/`: profile, settings, avatars, memories, notes, reference files, attachments, routines, SQLite stores, audit records.
- `shared/`: project data, local shared memory, workflows, frozen local transcripts.
- `workspace/`: workspace files with configurable large-directory inclusion.
- `conversations/<display-name>/`: monthly Markdown and original JSONL from the platform-synced search index.
- `platform-memory/`: human-readable RecallMemory drops, plus raw JSONL under agent and shared directories.
- `meta/MANIFEST.json`, `meta/workspace-copy.json`, `meta/backup-program/`: inventory, exclusions, source code and version.

Note that the raw export still reflects platform-specific data structures. A human-readable export helps handovers but is not a byte-for-byte replacement for the raw state.

## Data availability and limitations

| Source | What is available | Caveat |
| --- | --- | --- |
| `agent-data/search-index.db` | Platform-synced messages and media | Consistent SQLite snapshot; visible chat text, not complete tool execution |
| `agent-transcripts/<id>/<id>.jsonl`, `store.db` | Frozen local transcript and tool-call history | On the original box, updates stopped around September/October 2026 |
| `agents/<id>/` | Profiles, local memory, notes, attachments | Local memory may be stale |
| `memory-drop/` | Optional exported RecallMemory facts | Missing drops are reported as **not exported**, not manufactured or silently claimed to exist |
| `/workspace` | Accessible work products | Large directory inclusion is configurable |

These observations are based on the original host environment, **not universal claims about all Grok Bot installations**. Check the version and contents of your own box.

**No private-content filtering:** all available notes, conversations, attachments, and included workspace data are backed up without excluding content based on sensitivity. The old `--include-private` and `--passphrase` arguments are accepted only for compatibility and **do nothing**. They do **not** enable encryption.

**Backup destination and security are your responsibility.** The exports may contain personal data, credentials or tokens. The script applies restrictive file permissions (directories `0700`, files `0600`), but **does not encrypt** exports. Do not publish real backup archives to a public repository.

## Verification and portability

To preserve as much as the platform exposes, check assistant counts, full conversations, memory drops, notes, attachments, and skipped directories. Inspect:

- `meta/MANIFEST.json` for the raw export inventory.
- `meta/workspace-copy.json` for skipped workspace paths.
- `memory-index.md` for exported versus missing RecallMemory drops.
- The `99-backup-program/` or `meta/backup-program/` source snapshot and `COMMIT` file.

This tool has been used in an actual assistant environment in an earlier version; the **repackaged open-source version requires fresh testing on the target box**. Always verify a newly run backup before depending on it for disaster recovery.

## Source layout

- `bin/export-virtual-office.sh`: human-readable export.
- `bin/export-grok-bot-backup.sh`: raw backup and manifest.
- `bin/verify-export.sh`: verification.
- `nuwa_backup/conversations.py`: export message and media index.
- `nuwa_backup/memory_drop.py`: RecallMemory drop conversion.
- `nuwa_backup/virtual_office.py`: portable folder tree.
- `nuwa_backup/workspace.py`: workspace exclusions and copy reports.
- `nuwa_backup/program.py`: source and commit snapshot.
- `tests/`: lightweight regression tests.

## License

MIT. See [LICENSE](LICENSE).
