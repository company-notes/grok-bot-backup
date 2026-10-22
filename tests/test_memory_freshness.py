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

from grok_bot_backup import verify


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


if __name__ == "__main__":
    unittest.main()
