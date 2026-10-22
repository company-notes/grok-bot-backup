"""Embed a copy of this backup program (repo at HEAD + commit hash) in an export."""
from __future__ import annotations

import io
import pathlib
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

from .common import REPO_ROOT, write


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def program_info() -> dict:
    try:
        commit = _git("rev-parse", "HEAD")
        status = _git("status", "--porcelain")
        log = _git("log", "--oneline", "-n", "30")
        describe = _git("log", "-1", "--format=%cI %s")
    except Exception as e:  # not a git checkout
        return {"commit": None, "dirty": None, "error": str(e)}
    return {"commit": commit, "dirty": bool(status), "status": status, "log": log, "head": describe}


def snapshot_program(dest: pathlib.Path) -> dict:
    """dest/source/ = `git archive HEAD`; dest/COMMIT; dest/VERSION.md."""
    dest.mkdir(parents=True, exist_ok=True)
    info = program_info()
    if info.get("commit"):
        data = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", "HEAD"],
            check=True, capture_output=True,
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(dest / "source", filter="data")
        (dest / "COMMIT").write_text(info["commit"] + "\n", encoding="utf-8")
    dirty_note = ""
    if info.get("dirty"):
        dirty_note = (
            "\n> ⚠️ The working tree contains uncommitted changes. `source/` contains the HEAD revision; "
            "the executed program may differ:\n\n```\n" + info.get("status", "") + "\n```\n"
        )
    write(
        dest / "VERSION.md",
        f"""# Backup program used for this export

- Repository: `{REPO_ROOT}`
- Commit: `{info.get('commit')}`
- HEAD: {info.get('head', '')}
- Uncommitted changes: {info.get('dirty')}
- Generated at (UTC): {datetime.now(timezone.utc).isoformat()}
- Python: {sys.version.split()[0]}
{dirty_note}
`source/` contains the complete source code of this commit (`git archive HEAD`); use it to rerun or audit the export.

## Recent commits

```
{info.get('log', '')}
```
""",
    )
    return info


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 -m grok_bot_backup.program <dest-dir>")
    i = snapshot_program(pathlib.Path(sys.argv[1]))
    print(f"PROGRAM_COMMIT={i.get('commit')}")
    print(f"PROGRAM_DIRTY={i.get('dirty')}")
