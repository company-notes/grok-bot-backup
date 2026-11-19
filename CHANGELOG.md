# Changelog

## Unreleased

- **Home directory backup (on by default):** the box home directory (`BOX_HOME`, default `/home/box`) is copied into `home/` (raw) and `00-shared/box-home/` (Virtual Office), so credential folders, tool settings, MCP sign-in data and assistant-created folders outside `WORKSPACE`/`AGENT_DATA` are no longer lost. **This includes password and token files, unencrypted.**
  - Omitted: `AGENT_DATA` and its resolved target, `WORKSPACE` inside home, `OUT_ROOT`, earlier backups; by default also `.cache`, `.npm`, `.local/lib`, `.local/include`, `chrome-profile` and bulky folders.
  - Settings: `INCLUDE_HOME=0`, `HOME_EXCLUDE_PATHS`, `HOME_INCLUDE_PATHS`. Symlinks are kept, not followed; unreadable paths are reported and skipped.
  - `home-copy.json` reports copied entries, omissions with reasons and sizes, and unreadable paths; the verifier checks the copy against it.
- Optional per-file size limit, `ARCHIVE_SPLIT_SIZE` (for example `90MiB` for an upload tool with a 100 MB cap). Unset keeps the previous single-ZIP output.
  - Raw export: a larger archive is written as a standard split ZIP (`NAME.z01` … `NAME.zip`, APPNOTE §8.5 / Info-ZIP `zip -s` format), implemented in pure Python with ZIP64 support; opens with 7-Zip / WinRAR.
  - Virtual Office: a larger archive is written as several complete ZIP packs (`NAME-1ofN.zip` …) that each open on their own and merge into one tree; a single file above the limit gets its own split-ZIP pack.
  - Plain-text `NAME-RESTORE.txt` and `NAME-SHA256SUMS.txt` (plus `NAME-CONTENTS.tsv` for packs) are written next to divided output.
  - `python3 -m grok_bot_backup.archive join NAME.zip OUT.zip` rebuilds an ordinary ZIP from a split archive.
- **Review fixes (PR #2):**
  - `INCLUDE_HOME=1` with a missing or non-directory `BOX_HOME` now stops both exporters with an error (it used to skip silently and still verify as `RESULT OK`); `INCLUDE_HOME=0` remains the way to skip. The verifier fails old reports that recorded a missing home directory.
  - Home copy integrity: each file is re-read and matched against the source bytes (size and SHA-256); write errors, mismatches, or a finished tree whose file/link/byte totals differ stop the backup. `home-copy.json` records `file_hashes`, which the verifier rechecks (missing, changed or extra files fail).
  - Earlier backups are recognised by split volume (`.z01` … `.z99`, `.z100` …, `.001`-style parts) and sidecar (`-RESTORE.txt`, `-SHA256SUMS.txt`, `-CONTENTS.tsv`) as well as folder and archive, so WORKSPACE / HOME copies never re-include them.
  - `.gitignore` excludes every generated backup output (folders, archives, volumes, parts, sidecars, memory drops) so credential-bearing backups cannot be committed to this public repository by accident.
- Verifier: `--archive` accepts split ZIPs and Virtual Office packs, checks every part, compares archive members with the export folder, and checks `SHA256SUMS` when present.
- Docs: recommend a fixed parent folder with one `YYYY-MM-DD` subfolder per backup run when storing archives on Google Drive or other remote storage.


## ZIP default — 2026-10-23

- Both raw and Virtual Office exports now produce Windows-friendly `.zip` archives using Python's standard library, with ZIP64 and UTF-8 support.
- Preserve symlink entries without dereferencing them; use the original file copies for Unix-specific recovery.
- The verifier accepts and checks new ZIP archives and retains support for older `.tar.gz` backups.
- Add extraction instructions for Windows and regression tests for ZIP content.


## 2026-10-22 — Full agent-tree and previous-export safeguards

- Preserve all accessible entries under `agent-data/agents/`, including non-assistant platform folders and root-level metadata, without counting them as assistants.
- Ignore organization/people/procedures identifiers in assistant conversation lists and verification rows, while retaining the full SQLite search index snapshot in raw archives.
- Exclude legacy `grok-bot-export-*` and `virtual-office-*` directories and archives anywhere in the workspace, regardless of the configured output directory.
- Add end-to-end tests that run the actual raw export command and verify source preservation, conversation counts, and archive provenance.
- Keep built-in managed/plugin skills outside the export scope; user-defined workflows are preserved.


## Initial public release — 2026-10-22

- Export complete, accessible Grok Bot data as a raw archive and a human-readable Virtual Office package.
- Export platform-synced conversation history, available RecallMemory drops (phase 1), and backup verification reports.
- Configure site-specific assistant names/IDs in `config.json`, with paths controlled by environment variables.
- Add `INCLUDE_BULKY_DIRS` to optionally include large workspace dependencies and caches.
