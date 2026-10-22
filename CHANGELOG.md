# Changelog

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
