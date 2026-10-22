"""End-to-end raw backup regression test using a tiny fake Grok Bot box.

Runs the real executable and verifies that non-assistant platform data is
retained without appearing as assistant conversations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest


AGENT_ID = "00000000-0000-0000-0000-000000000001"


class RawAgentTreeTests(unittest.TestCase):
    def test_raw_export_keeps_all_non_agent_source_files(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            data = root / "agent-data"
            agents = data / "agents"
            (agents / AGENT_ID / "private").mkdir(parents=True)
            (agents / AGENT_ID / "profile.json").write_text('{"name":"Test assistant"}')
            (agents / AGENT_ID / "private" / "note.txt").write_text("private original content")
            (agents / AGENT_ID / "notes").mkdir()
            (agents / AGENT_ID / "notes" / "memo.md").write_text("original note")
            source_files = {
                "organization/profile.json": "{}",
                "organization/store.db": "organization raw database",
                "people/profile.json": "{}",
                "people/store.db": "people raw database",
                "procedures/profile.json": "{}",
                "procedures/store.db": "procedures raw database",
                "active-agent.json": '{"active":true}',
                "audit-outbox.json": '{"queued":[]}',
            }
            for name, data_text in source_files.items():
                p = agents / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(data_text)

            conn = sqlite3.connect(data / "search-index.db")
            conn.execute("create table agents (agent_id text)")
            conn.execute("""create table messages (
                id integer primary key, agent_id text, seq integer, entry_id text,
                role text, timestamp_ms integer, body text
            )""")
            conn.execute("""create table media (
                id integer primary key, agent_id text, seq integer, entry_id text,
                timestamp_ms integer, file_name text, kind text, ext text
            )""")
            conn.executemany("insert into agents (agent_id) values (?)",
                             [(AGENT_ID,), ("organization",), ("people",), ("procedures",)])
            conn.execute(
                "insert into messages (agent_id, seq, entry_id, role, timestamp_ms, body)"
                " values (?, 1, 'valid-message', 'user', 1792600000000, 'Hello')",
                (AGENT_ID,),
            )
            conn.execute(
                "insert into messages (agent_id, seq, entry_id, role, timestamp_ms, body)"
                " values ('organization', 1, 'platform-record', 'assistant', 1792600000000, 'raw platform record')"
            )
            conn.commit()
            conn.close()

            output = workspace / "exports"
            env = os.environ.copy()
            env.update({
                "AGENT_DATA": str(data),
                "WORKSPACE": str(workspace),
                "OUT_ROOT": str(output),
                "MEMORY_DROP": str(workspace / "empty-memory-drop"),
                "BACKUP_EXCLUDE_PATHS": "",
                "INCLUDE_BULKY_DIRS": "0",
            })
            exported = subprocess.run(
                [str(repo / "bin" / "export-grok-bot-backup.sh")],
                cwd=repo, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(exported.returncode, 0,
                             exported.stdout + "\n" + exported.stderr)
            manifests = list(output.glob("grok-bot-export-*/meta/MANIFEST.json"))
            self.assertEqual(len(manifests), 1)
            folder = manifests[0].parents[1]

            for name, expected in source_files.items():
                with self.subTest(name=name):
                    self.assertEqual((folder / "agents" / name).read_text(), expected)
            self.assertEqual(
                (folder / "agents" / AGENT_ID / "private" / "note.txt").read_text(),
                "private original content",
            )
            report = json.loads(manifests[0].read_text())
            self.assertEqual(report["agent_count"], 1)
            self.assertEqual([p["id"] for p in report["agents"]], [AGENT_ID])
            stats = json.loads(
                (folder / "conversations" / "conversation-stats.json").read_text()
            )
            self.assertEqual([p["agent_id"] for p in stats["agents"]], [AGENT_ID])
            self.assertEqual(stats["agents"][0]["messages"], 1)
            snapshot = folder / "conversations" / "search-index-snapshot.db"
            self.assertTrue(snapshot.is_file())
            with sqlite3.connect(snapshot) as copied:
                self.assertEqual(
                    copied.execute("select count(*) from messages").fetchone()[0], 2
                )

            verified = subprocess.run(
                [str(repo / "bin" / "verify-export.sh"), str(folder)],
                cwd=repo, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(verified.returncode, 0,
                             verified.stdout + "\n" + verified.stderr)
            self.assertIn("RESULT OK", verified.stdout)


if __name__ == "__main__":
    unittest.main()
