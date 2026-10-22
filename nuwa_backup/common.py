"""Shared paths and helpers.

2026-10-09: Owner decided to remove the private rules; every backup is a full backup.
The old --include-private / --passphrase flags are accepted and ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone

AGENT_DATA = pathlib.Path(os.environ.get("AGENT_DATA", "/home/box/agent-data"))
WORKSPACE = pathlib.Path(os.environ.get("WORKSPACE", "/workspace"))
OUT_ROOT = pathlib.Path(os.environ.get("OUT_ROOT", str(WORKSPACE / "exports")))
SEARCH_INDEX = AGENT_DATA / "search-index.db"
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def taipei(ts_ms: int | None) -> datetime | None:
    if not ts_ms:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, TAIPEI)


def fmt_taipei(ts_ms: int | None, with_seconds: bool = True) -> str:
    dt = taipei(ts_ms)
    if dt is None:
        return "(no timestamp)"
    return dt.strftime("%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- deprecated flags
DEPRECATION_NOTICE = ("NOTICE: --include-private/--passphrase are deprecated and ignored; "
                      "every backup now includes everything (2026-10-09).")


def add_deprecated_private_args(parser: argparse.ArgumentParser) -> None:
    """Old flags still parse (so existing commands keep working) but do nothing."""
    parser.add_argument("--include-private", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--passphrase", default=None, help=argparse.SUPPRESS)


def warn_deprecated_private_args(args) -> None:
    if getattr(args, "include_private", False) or getattr(args, "passphrase", None) is not None:
        print(DEPRECATION_NOTICE, file=sys.stderr)


# --------------------------------------------------------------------------- fs helpers
def safe_name(name: str) -> str:
    name = (name or "Unnamed").strip()
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:80] or "Unnamed"


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def copy_if(src: pathlib.Path, dst: pathlib.Path) -> None:
    if src.exists() and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def lock_down(root: pathlib.Path) -> None:
    """Directories 700, files 600 (exports may contain tokens from old chats)."""
    if root.is_file():
        root.chmod(0o600)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o700)
        for f in filenames:
            p = os.path.join(dirpath, f)
            if not os.path.islink(p):
                os.chmod(p, 0o600)


# --------------------------------------------------------------------------- agents
def load_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def deleted_agent_ids() -> dict:
    data = load_json(AGENT_DATA / "deleted-agents.json", {}) or {}
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def valid_agent_id(name: str) -> bool:
    """Accept UUID-like agent IDs, never folders such as organization/people."""
    return bool(re.fullmatch(
        r"(?:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})",
        name,
    ))


def agent_registry() -> dict[str, dict]:
    """id -> {id, name, short, kind(agent|group), dir, display_folder}.

    Folder names follow the old Virtual Office export (safe_name(profile name)).
    """
    reg: dict[str, dict] = {}
    root = AGENT_DATA / "agents"
    if not root.exists():
        return reg
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not valid_agent_id(d.name):
            continue
        prof = load_json(d / "profile.json", None)
        if prof is None:
            continue
        is_group = (d / "group.json").exists()
        name = prof.get("name") or d.name
        if not is_group and name.strip() == "New Bot" and not (prof.get("description") or "").strip():
            name = "New Bot (not configured)"
        short = name.split(" | ")[0].strip() or name
        reg[d.name] = {
            "id": d.name,
            "name": name,
            "short": short,
            "kind": "group" if is_group else "agent",
            "dir": d,
            "folder": safe_name(name),
        }
    return reg


def unknown_agent_entry(agent_id: str) -> dict:
    deleted = agent_id in deleted_agent_ids()
    label = f"(Deleted assistant) {agent_id[:8]}" if deleted else f"(Unknown assistant) {agent_id[:8]}"
    return {
        "id": agent_id,
        "name": label,
        "short": "Assistant",
        "kind": "deleted" if deleted else "unknown",
        "dir": None,
        "folder": safe_name(label),
    }
