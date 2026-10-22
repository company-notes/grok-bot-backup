"""Regression tests for English-language backup reports and source files."""
from pathlib import Path
import unittest

from nuwa_backup import conversations, memory_drop


class EnglishOutputTests(unittest.TestCase):
    def test_monthly_conversations_keep_original_text_and_use_english_labels(self):
        info = {"name": "Example Agent", "kind": "agent", "short": "Example Agent"}
        body = "Verbatim message body, even if it is not English."
        rows = [(1, "example-entry", "user", 1792627200000, body)]
        output = conversations.render_month(info, "2026-10", rows, {})
        self.assertIn("Conversations 2026-10", output)
        self.assertIn("Messages this month", output)
        self.assertIn("User", output)
        self.assertIn(body, output)

    def test_memory_report_uses_english_labels(self):
        data = [{"date": "2026-10-22", "tier": "profile", "scope": "user",
                 "source": "self", "fact": "Original memory content."}]
        output = memory_drop.render_markdown(data, "Example Memory", "Description")
        self.assertIn("Persistent (profile)", output)
        self.assertIn("Original memory content.", output)

    def test_source_has_no_han_characters(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ("nuwa_backup", "bin", "tests"):
            for path in (root / folder).rglob("*"):
                if not path.is_file() or path.suffix not in (".py", ".sh"):
                    continue
                with self.subTest(path=str(path)):
                    content = path.read_text(encoding="utf-8")
                    self.assertFalse(
                        any("\u4e00" <= ch <= "\u9fff" for ch in content),
                        f"{path} still contains untranslated Chinese text",
                    )


if __name__ == "__main__":
    unittest.main()
