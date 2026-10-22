"""Phase-1 platform memory: copy peer/self RecallMemory drops into a backup.

Drops live in /workspace/exports/memory-drop/ (not produced by this module).
Missing drops are recorded as not exported and never fail the backup.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil

from .common import AGENT_DATA, lock_down, safe_name, write

MEMORY_DROP_DEFAULT = pathlib.Path(os.environ.get("WORKSPACE", "/workspace")) / "exports" / "memory-drop"

# (id, display name, required). Loaded from a site config file, never hardcoded.
# Set NUWA_BACKUP_CONFIG=/path/to/config.json (default: ./config.json next to the repo).
# See config.example.json.
def _load_expected_agents() -> list[tuple[str, str, bool]]:
    cfg = pathlib.Path(os.environ.get(
        "NUWA_BACKUP_CONFIG",
        str(pathlib.Path(__file__).resolve().parent.parent / "config.json")))
    if not cfg.is_file():
        return []
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return [(a["id"], a.get("name", a["id"]), bool(a.get("required", True)))
            for a in data.get("expected_agents", [])]


EXPECTED_AGENTS: list[tuple[str, str, bool]] = _load_expected_agents()

TIER_ORDER = ("profile", "log", "note")
TIER_TITLE = {
    "profile": "常駐（profile）",
    "log": "自有（log）",
    "note": "自有（note）",
}


def drop_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("MEMORY_DROP", str(MEMORY_DROP_DEFAULT)))


def load_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rows.append({
                "date": "",
                "tier": "note",
                "scope": "",
                "fact": f"（第 {i} 行不是合法 JSON，已略過解析、原文保留在 jsonl）",
                "source": "unparsed",
            })
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _tier_key(row: dict) -> str:
    tier = str(row.get("tier") or "").strip()
    scope = str(row.get("scope") or "").strip()
    if tier in TIER_TITLE:
        return tier
    if scope == "conversation" or tier == "conversation":
        return "conversation"
    return tier or "unknown"


def render_markdown(rows: list[dict], title: str, intro: str) -> str:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(_tier_key(row), []).append(row)
    order = list(TIER_ORDER) + ["conversation"]
    order += [k for k in buckets if k not in order]
    parts = [f"# {title}", "", intro.rstrip(), ""]
    if not rows:
        parts.append("（沒有事實）")
        parts.append("")
        return "\n".join(parts)
    titles = {
        **TIER_TITLE,
        "conversation": "對話範圍（conversation）",
        "unknown": "未標層級",
    }
    for key in order:
        group = buckets.get(key) or []
        if not group:
            continue
        parts.append(f"## {titles.get(key, key)}")
        parts.append("")
        for row in group:
            date = str(row.get("date") or "（無日期）")
            scope = str(row.get("scope") or "（無 scope）")
            source = str(row.get("source") or "self")
            fact = str(row.get("fact") or "").rstrip()
            parts.append(f"### {date} · {scope} · {source}")
            parts.append("")
            parts.append(fact if fact else "（空白）")
            parts.append("")
    return "\n".join(parts)


def _agent_drop_files(agent_dir: pathlib.Path) -> list[pathlib.Path]:
    if not agent_dir.is_dir():
        return []
    preferred = ["memory-profile.jsonl", "memory-agent.jsonl"]
    found = []
    for name in preferred:
        p = agent_dir / name
        if p.is_file():
            found.append(p)
    for p in sorted(agent_dir.glob("*.jsonl")):
        if p not in found:
            found.append(p)
    return found


def _display_folder(agent_id: str, id_to_name: dict[str, str] | None) -> tuple[str, str]:
    """Return (top folder '01-assistants'|'02-groups', safe display folder name)."""
    name = None
    if id_to_name and agent_id in id_to_name:
        name = id_to_name[agent_id]
    if not name:
        for aid, label, _req in EXPECTED_AGENTS:
            if aid == agent_id:
                name = label
                break
    if not name:
        name = agent_id
    kind = "01-assistants"
    g = AGENT_DATA / "agents" / agent_id / "group.json"
    if g.exists():
        kind = "02-groups"
    return kind, safe_name(name)


def _copy_jsonl(src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def scan(root: pathlib.Path | None = None) -> dict:
    """Status only: counts, no fact text."""
    root = root or drop_root()
    shared = root / "shared-user" / "memory-user.jsonl"
    shared_n = len(load_jsonl(shared)) if shared.is_file() else None
    agents = []
    for aid, label, required in EXPECTED_AGENTS:
        d = root / "agents" / aid
        files = _agent_drop_files(d)
        counts = {p.name: len(load_jsonl(p)) for p in files}
        agents.append({
            "id": aid,
            "name": label,
            "required": required,
            "dropped": bool(files),
            "files": counts,
        })
    # unexpected ids
    extra = []
    agents_root = root / "agents"
    known = {a[0] for a in EXPECTED_AGENTS}
    if agents_root.is_dir():
        for d in sorted(agents_root.iterdir()):
            if d.is_dir() and d.name not in known and _agent_drop_files(d):
                extra.append(d.name)
    return {
        "drop_root": str(root),
        "drop_exists": root.is_dir(),
        "shared_user": None if shared_n is None else {"path": str(shared), "count": shared_n},
        "agents": agents,
        "extra_ids": extra,
    }


def _index_markdown(status: dict) -> str:
    lines = [
        "# 平台記憶索引（phase 1）",
        "",
        "來源：`/workspace/exports/memory-drop/`（RecallMemory 投放）。",
        "沒有投放的助理標「not exported」，不中斷備份。",
        "本機 `memory/` 檔（停在 2026-10-06）仍另外保留，與此處的平台記憶分開。",
        "",
        "## 共用記憶（scope=user）",
        "",
    ]
    if status["shared_user"] is None:
        lines.append("- not exported")
    else:
        lines.append(f"- exported：{status['shared_user']['count']} 則 → `00-shared/記憶/`")
    lines += ["", "## 各助理", ""]
    for a in status["agents"]:
        tag = "" if a["required"] else "（選用）"
        if not a["dropped"]:
            lines.append(f"- {a['name']}{tag}：not exported")
        else:
            bits = ", ".join(f"{k} {n} 則" for k, n in a["files"].items())
            lines.append(f"- {a['name']}{tag}：exported（{bits}）")
    if status["extra_ids"]:
        lines += ["", "## 名冊以外的投放", ""]
        for i in status["extra_ids"]:
            lines.append(f"- `{i}`")
    lines.append("")
    return "\n".join(lines)


def apply_tree(dest: pathlib.Path, id_to_name: dict[str, str] | None = None) -> dict:
    """Write human-readable memory into dest/00-shared and dest/01-assistants|02-groups.

    dest is the virtual-office root, or grok's platform-memory root (same layout).
    """
    root = drop_root()
    status = scan(root)
    write(dest / "memory-index.md", _index_markdown(status))

    shared_src = root / "shared-user" / "memory-user.jsonl"
    shared_dir = dest / "00-shared" / "記憶"
    if shared_src.is_file():
        rows = load_jsonl(shared_src)
        _copy_jsonl(shared_src, shared_dir / "memory-user.jsonl")
        write(shared_dir / "shared-memory.md", render_markdown(
            rows,
            "共用記憶",
            "scope=user，所有助理共用。每則含日期、scope、source 與全文，可用 grep 搜尋。",
        ))
        write(shared_dir / "README.md", """# 共用記憶

| 檔案 | 說明 |
|---|---|
| `shared-memory.md` | 人可讀，依 tier 分節（常駐 profile／自有 log／note） |
| `memory-user.jsonl` | 原始投放，一行一則 |

本機凍結檔仍在 `../shared-agent-memory/`（若 virtual-office 包）或 grok export 的 `shared/user-memory/`。
""")
    else:
        write(shared_dir / "README.md", "# 共用記憶\n\nnot exported\n")

    seen: set[str] = set()
    for a in status["agents"]:
        seen.add(a["id"])
        _write_agent(dest, a["id"], a["name"], root / "agents" / a["id"], id_to_name, dropped=a["dropped"])
    for aid in status["extra_ids"]:
        _write_agent(dest, aid, aid, root / "agents" / aid, id_to_name, dropped=True)
    return status


def _write_agent(dest, agent_id, label, drop_dir, id_to_name, dropped: bool) -> None:
    kind, folder = _display_folder(agent_id, id_to_name)
    # prefer an existing folder under either tree (export already created it)
    existing = None
    for top in ("01-assistants", "02-groups"):
        cand = dest / top / folder
        if cand.is_dir():
            existing = cand
            break
    base = existing if existing is not None else (dest / kind / folder)
    mem = base / "記憶"
    if not dropped:
        # only note inside the pack index; don't invent empty per-agent files
        # unless that agent's folder already exists (so a reader opening 記憶/ sees the gap)
        if existing is not None:
            note = mem / "platform-memory.md"
            if not note.exists():
                write(note, f"# 平台記憶\n\nnot exported\n\n（{label}，`{agent_id}`）\n")
        return
    files = _agent_drop_files(drop_dir)
    rows: list[dict] = []
    for src in files:
        _copy_jsonl(src, mem / src.name)
        rows.extend(load_jsonl(src))
    write(mem / "platform-memory.md", render_markdown(
        rows,
        f"平台記憶 · {label}",
        "由該助理用 RecallMemory 投放。常駐＝tier profile；自有＝tier log／note。"
        " 每則含日期與全文。jsonl 為原始列。",
    ))


def apply_grok(dest: pathlib.Path) -> dict:
    """Human-readable tree at dest/platform-memory/, plus jsonl beside each agent id."""
    status = apply_tree(dest / "platform-memory", id_to_name=None)
    root = drop_root()
    shared_src = root / "shared-user" / "memory-user.jsonl"
    if shared_src.is_file():
        target = dest / "shared" / "platform-memory"
        target.mkdir(parents=True, exist_ok=True)
        _copy_jsonl(shared_src, target / "memory-user.jsonl")
    agents_root = root / "agents"
    if agents_root.is_dir():
        for d in agents_root.iterdir():
            if not d.is_dir():
                continue
            files = _agent_drop_files(d)
            if not files:
                continue
            out = dest / "agents" / d.name / "platform-memory"
            out.mkdir(parents=True, exist_ok=True)
            for src in files:
                _copy_jsonl(src, out / src.name)
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy memory-drop into an export tree")
    ap.add_argument("--into-grok", type=pathlib.Path, help="grok-bot-export directory")
    ap.add_argument("--into-virtual", type=pathlib.Path, help="virtual-office directory")
    ap.add_argument("--status", action="store_true", help="print counts only")
    args = ap.parse_args()
    if args.status or not (args.into_grok or args.into_virtual):
        st = scan()
        print(json.dumps({
            "drop_exists": st["drop_exists"],
            "shared_user": None if st["shared_user"] is None else st["shared_user"]["count"],
            "agents": [
                {"id": a["id"], "name": a["name"], "dropped": a["dropped"], "files": a["files"]}
                for a in st["agents"]
            ],
            "extra_ids": st["extra_ids"],
        }, ensure_ascii=False, indent=2))
        return
    if args.into_virtual:
        apply_tree(args.into_virtual)
        lock_down(args.into_virtual)
        print(f"VIRTUAL_MEMORY={args.into_virtual}")
    if args.into_grok:
        apply_grok(args.into_grok)
        lock_down(args.into_grok)
        print(f"GROK_MEMORY={args.into_grok}")


if __name__ == "__main__":
    main()
