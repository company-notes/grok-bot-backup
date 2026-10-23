"""Check memory export freshness warnings and --strict enforcement."""
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from grok_bot_backup import verify, memory_drop


AGENT_ID = "00000000-0000-0000-0000-000000000001"
CUTOFF = datetime(2026, 10, 22, 9, 0, tzinfo=timezone.utc).timestamp()


class MemoryFreshnessTests(unittest.TestCase):
    def _fixture(self, root: Path, age_days: float):
        agent_data = root / "agent-data"
        agent_data.mkdir()
        conn = sqlite3.connect(agent_data / "search-index.db")
        conn.execute("CREATE TABLE messages (agent_id TEXT, entry_id TEXT, timestamp_ms INTEGER)")
        conn.commit()
        conn.close()
        export = root / "virtual-office"
        convo = export / "01-assistants" / "Test Agent" / "conversations"
        convo.mkdir(parents=True)
        stats = {
            "cutoff_ms": int(CUTOFF * 1000),
            "cutoff_taipei": "2026-10-22 17:00:00",
            "agents": [{
                "agent_id": AGENT_ID, "name": "Test Agent", "kind": "agent",
                "conv_dir": str(convo.relative_to(export)),
                "messages": 0, "first": None, "last": None,
            }],
        }
        prog = export / "99-backup-program"
        (prog / "source" / "grok_bot_backup").mkdir(parents=True)
        (prog / "source" / "grok_bot_backup" / "common.py").write_text("# demo")
        (prog / "COMMIT").write_text("a" * 40)
        (prog / "conversation-stats.json").write_text(json.dumps(stats))
        (export / "memory-index.md").write_text("# Memory index\n\nnot exported\n")
        mem = convo.parent / "memory"
        mem.mkdir()
        file = mem / "memory-agent.jsonl"
        file.write_text('{"date":"2026-10-22","tier":"note","scope":"agent","fact":"example"}\n')
        timestamp = CUTOFF - age_days * 86400
        os.utime(file, (timestamp, timestamp))
        return export, agent_data

    def _run(self, export: Path, agent_data: Path, strict: bool):
        args = ["verify", str(export)] + (["--strict"] if strict else [])
        buf = io.StringIO()
        with patch.object(sys, "argv", args), \
             patch.object(verify, "SEARCH_INDEX", agent_data / "search-index.db"), \
             patch.object(verify, "AGENT_DATA", agent_data), \
             patch.dict(os.environ, {"MEMORY_MAX_AGE_DAYS": "2",
                                    "MEMORY_DROP": str(agent_data / "missing-drop")}):
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit) as outcome:
                    verify.main()
        return outcome.exception.code, buf.getvalue()

    def test_stale_memory_warns_but_strict_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=5)
            code, output = self._run(export, data, False)
            self.assertEqual(code, 0)
            self.assertIn("WARN Test Agent", output)
            self.assertIn("5.00 days", output)
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("FAIL Test Agent", output)

    def test_recent_memory_passes_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            code, output = self._run(export, data, True)
            self.assertEqual(code, 0)
            self.assertIn("MEMORY Test Agent", output)
            self.assertIn("file modified (UTC): 2026-10-21T09:00:00+00:00", output)


    def test_empty_jsonl_warns_and_strict_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            (export / "01-assistants/Test Agent/memory/memory-agent.jsonl").write_text("")
            code, output = self._run(export, data, False)
            self.assertEqual(code, 0)
            self.assertIn("WARN Test Agent", output)
            self.assertIn("zero memory facts", output)
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("FAIL Test Agent", output)

    def test_empty_jsonl_with_recent_explicit_no_memory_confirmation_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            directory = export / "01-assistants/Test Agent/memory"
            (directory / "memory-agent.jsonl").write_text("")
            (directory / "memory-status.json").write_text(json.dumps({
                "status": "no_memories",
                "agent_id": AGENT_ID,
                "checked_at_utc": "2026-10-22T08:00:00Z",
            }))
            code, output = self._run(export, data, True)
            self.assertEqual(code, 0)
            self.assertIn("explicitly declared no memories", output)
            self.assertNotIn("WARN", output)

    def test_empty_marker_requires_correct_id_and_recent_check(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            directory = export / "01-assistants/Test Agent/memory"
            (directory / "memory-agent.jsonl").write_text("")
            marker = directory / "memory-status.json"
            marker.write_text(json.dumps({
                "status": "no_memories",
                "agent_id": "other-agent",
                "checked_at_utc": "2026-10-22T08:00:00Z",
            }))
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("invalid memory-status.json", output)
            marker.write_text(json.dumps({
                "status": "no_memories",
                "agent_id": AGENT_ID,
                "checked_at_utc": "2026-10-17T08:00:00Z",
            }))
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("declaration older than 2 days", output)

    def test_group_chat_without_memory_is_explicitly_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            group_id = "00000000-0000-0000-0000-000000000002"
            stats_path = export / "99-backup-program/conversation-stats.json"
            stats = json.loads(stats_path.read_text())
            group_conv = export / "02-groups" / "Company group" / "conversations"
            group_conv.mkdir(parents=True)
            stats["agents"].append({
                "agent_id": group_id, "name": "Company group", "kind": "group",
                "conv_dir": str(group_conv.relative_to(export)),
                "messages": 0, "first": None, "last": None,
            })
            stats_path.write_text(json.dumps(stats))
            code, output = self._run(export, data, True)
            self.assertEqual(code, 0)
            self.assertIn("SKIP MEMORY Company group: group chat", output)

    def test_group_memory_is_checked_if_actually_supplied(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            group_id = "00000000-0000-0000-0000-000000000002"
            stats_file = export / "99-backup-program/conversation-stats.json"
            stats = json.loads(stats_file.read_text())
            group_dir = export / "02-groups/Company group"
            (group_dir / "conversations").mkdir(parents=True)
            (group_dir / "memory").mkdir()
            (group_dir / "memory/memory-group.jsonl").write_text("")
            stats["agents"].append({
                "agent_id": group_id, "name": "Company group", "kind": "group",
                "conv_dir": str((group_dir / "conversations").relative_to(export)),
                "messages": 0, "first": None, "last": None,
            })
            stats_file.write_text(json.dumps(stats))
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("FAIL group Company group", output)

    def test_explicit_empty_declarations_are_preserved_by_both_exporters(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "memory-drop"
            agent_source = source / "agents" / AGENT_ID
            agent_source.mkdir(parents=True)
            shared = source / "shared-user"
            shared.mkdir()
            (agent_source / "memory-status.json").write_text(json.dumps({
                "status": "no_memories", "agent_id": AGENT_ID,
                "checked_at_utc": "2026-10-22T08:00:00Z",
            }))
            (shared / "memory-status.json").write_text(json.dumps({
                "status": "no_memories", "scope": "user",
                "checked_at_utc": "2026-10-22T08:00:00Z",
            }))
            with patch.object(memory_drop, "drop_root", return_value=source), \\
                 patch.object(memory_drop, "EXPECTED_AGENTS",
                              [(AGENT_ID, "Test Agent", True)]), \\
                 patch.object(memory_drop, "AGENT_DATA", base / "no-agent-data"):
                readable = base / "virtual-office"
                (readable / "01-assistants/Test Agent").mkdir(parents=True)
                memory_drop.apply_tree(readable, {AGENT_ID: "Test Agent"})
                self.assertTrue((readable /
                    "01-assistants/Test Agent/memory/memory-status.json").exists())
                self.assertTrue((readable /
                    "00-shared/memory/memory-status.json").exists())
                self.assertNotIn("not exported",
                    (readable / "00-shared/memory/README.md").read_text())

                raw = base / "grok-bot-export"
                memory_drop.apply_grok(raw)
                self.assertTrue((raw /
                    f"agents/{AGENT_ID}/platform-memory/memory-status.json").exists())
                self.assertTrue((raw /
                    "shared/platform-memory/memory-status.json").exists())

    def test_invalid_memory_jsonl_cannot_pass_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            file = export / "01-assistants/Test Agent/memory/memory-agent.jsonl"
            file.write_text('{"date":"2026-10-22", "fact":""}\\n')
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("malformed memory records", output)

    def test_empty_shared_memory_export_does_not_pass_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            export, data = self._fixture(Path(temp), age_days=1)
            shared = export / "00-shared" / "memory"
            shared.mkdir(parents=True)
            (shared / "memory-user.jsonl").write_text("")
            code, output = self._run(export, data, True)
            self.assertEqual(code, 1)
            self.assertIn("FAIL shared user memory", output)


if __name__ == "__main__":
    unittest.main()
