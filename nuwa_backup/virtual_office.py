#!/usr/bin/env python3
"""Export Grok Bot durable state as a human-readable Virtual Office pack.

Not for restoring Grok internals. Organized for people / any virtual office:
  00-shared/   01-assistants/<顯示名>/   02-groups/<群組名>/   99-backup-program/
Each assistant gets conversations/ with the full chat history (monthly Markdown)
from the platform-synced search index.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import tarfile

from . import conversations
from .memory_drop import apply_tree as apply_memory_drop
from .common import (
    AGENT_DATA, OUT_ROOT, WORKSPACE, add_deprecated_private_args, copy_if,
    lock_down, safe_name, utc_stamp, warn_deprecated_private_args, write,
)
from .program import snapshot_program
from .workspace import bulky, enabled as include_bulky_dirs

STAMP = utc_stamp()
DEST = OUT_ROOT / f"virtual-office-{STAMP}"


def export_agent(agent_dir: pathlib.Path, dest_agents: pathlib.Path, id_to_name: dict) -> str | None:
    profile_path = agent_dir / "profile.json"
    if not profile_path.exists() or (agent_dir / "group.json").exists():
        return None
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    name = profile.get("name") or agent_dir.name
    if name.strip() == "New Bot" and not (profile.get("description") or "").strip():
        name = "New Bot（not configured）"
    folder = dest_agents / safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)

    desc = profile.get("description") or ""
    title = profile.get("title") or ""
    settings = {}
    sp = agent_dir / "settings.json"
    if sp.exists():
        try:
            settings = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            settings = {}
    write(folder / "profile.md", f"""# {name}

- 內部 ID：`{agent_dir.name}`
- 職稱／Title：{title or "（無）"}
- 匯出時間（UTC）：{STAMP}

## 人物／角色設定

{desc.strip() or "（尚無描述）"}

## 設定摘要

```json
{json.dumps(settings, ensure_ascii=False, indent=2)}
```
""")

    for av in agent_dir.glob("avatar.*"):
        copy_if(av, folder / f"avatar{av.suffix.lower()}")

    # memory (local files; frozen since 2026-10-06 — see README)
    mem = agent_dir / "memory"
    if mem.exists():
        for p in mem.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "記憶" / p.relative_to(mem))
        parts = ["# 記憶彙整\n", "> 注意：本機記憶檔停在 2026-10-06 15:10，之後的記憶尚未納入備份。\n"]
        for p in sorted(mem.rglob("*.md")):
            parts.append(f"## {p.relative_to(mem)}\n\n{p.read_text(encoding='utf-8', errors='replace')}\n")
        write(folder / "memory-summary.md", "\n".join(parts))

    auto = agent_dir / "automations"
    if auto.exists() and any(auto.iterdir()):
        for p in auto.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "routines" / p.relative_to(auto))

    for src_name, dst_name in [("attachments", "attachments-and-work"), ("reference", "references")]:
        src = agent_dir / src_name
        if src.exists():
            for p in src.rglob("*"):
                if p.is_file() and ".uploads" not in p.parts:
                    copy_if(p, folder / dst_name / p.relative_to(src))

    # notes：全部依原資料夾結構匯出（2026-10-09 起沒有private規則）
    notes = agent_dir / "notes"
    if notes.exists():
        for p in notes.rglob("*"):
            if p.is_file():
                copy_if(p, folder / "notes-and-profiles" / p.relative_to(notes))

    write(folder / "README.md", f"""# {name}

本資料夾是可移植的「虛擬員工／角色」包，供人閱讀或匯入其他 virtual office。

| 檔案／資料夾 | 說明 |
|---|---|
| `profile.md` | 名稱、角色、行為準則 |
| `avatar.*` | avatar圖 |
| `conversations/` | **完整對話**（每月一個 .md，台北時間；`README.md` 為索引） |
| `記憶/`、`memory-summary.md` | 長期記憶（本機檔，停在 2026-10-06） |
| `notes-and-profiles/` | 助理筆記（全部，依原資料夾結構） |
| `attachments-and-work/` | 聊天附件與產出（hash 檔名；對照見 `conversations/attachment-index.md`） |
| `references/` | 角色參考文件 |
| `routines/` | routines（若有） |
""")
    id_to_name[agent_dir.name] = name
    return name


def export_group(group_dir: pathlib.Path, dest_groups: pathlib.Path, id_to_name: dict) -> None:
    profile = json.loads((group_dir / "profile.json").read_text(encoding="utf-8"))
    group = json.loads((group_dir / "group.json").read_text(encoding="utf-8"))
    name = profile.get("name") or group_dir.name
    folder = dest_groups / safe_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    members = group.get("memberIds") or []
    member_lines = [f"- {id_to_name.get(mid, mid)}（`{mid}`）" for mid in members]
    write(folder / "group-profile.md", f"""# 群組：{name}

- 內部 ID：`{group_dir.name}`
- 匯出時間（UTC）：{STAMP}

## 成員

{chr(10).join(member_lines) or "（無）"}

## 說明

{profile.get("description") or "（無額外描述）"}
""")
    mem = group_dir / "memory"
    if mem.exists():
        for p in mem.rglob("*.md"):
            copy_if(p, folder / "記憶" / p.relative_to(mem))
    write(folder / "README.md", f"""# 群組：{name}

人可讀群組包。成員名單見 `group-profile.md`；群內對話見 `conversations/`（若有）。
""")


def export_shared(dest_shared: pathlib.Path) -> None:
    dest_shared.mkdir(parents=True, exist_ok=True)
    with_bulky = include_bulky_dirs()
    company = WORKSPACE / "company"
    if company.exists():
        for p in company.rglob("*"):
            if p.is_file() and "exports" not in p.parts:
                rel = p.relative_to(company)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                copy_if(p, dest_shared / "company" / rel)
    proj = AGENT_DATA / "projects" / "nuwa-company"
    if proj.exists():
        write(dest_shared / "company" / "project-overview.md",
              (proj / "project.md").read_text(encoding="utf-8") if (proj / "project.md").exists() else "")
        mem_root = proj / "memory" / "by-agent"
        if mem_root.exists():
            for shard in mem_root.iterdir():
                if shard.is_dir():
                    for p in shard.rglob("*.md"):
                        copy_if(p, dest_shared / "company" / "project-memory" / shard.name / p.relative_to(shard))
    um = AGENT_DATA / "user-memory"
    if um.exists():
        for p in um.rglob("*.md"):
            copy_if(p, dest_shared / "shared-agent-memory" / p.relative_to(um))
    wf = AGENT_DATA / "workflows"
    if wf.exists():
        for p in wf.rglob("*"):
            if p.is_file():
                copy_if(p, dest_shared / "skills-and-workflows" / p.relative_to(wf))
    wp = dest_shared / "workspace-outputs"
    for item in WORKSPACE.iterdir():
        if item.name in {"exports", "scripts", "uploads", "company"}:
            continue
        if not with_bulky and bulky(item.name) and item.is_dir():
            continue
        if item.is_file():
            copy_if(item, wp / item.name)
        elif item.is_dir():
            for p in item.rglob("*"):
                if p.is_file() and (with_bulky or not any(bulky(part) for part in p.relative_to(item).parts)):
                    copy_if(p, wp / item.name / p.relative_to(item))
    write(dest_shared / "README.md", """# 共用區

| 資料夾 | 說明 |
|---|---|
| `company/` | company理念、職責、project-memory、原始簡報 |
| `shared-agent-memory/` | 所有助理都會帶的短事實（本機檔，停在 2026-10-06） |
| `skills-and-workflows/` | 可重用技能 |
| `workspace-outputs/` | 共用電腦上的工作成果（記帳等） |
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Virtual Office readable pack")
    add_deprecated_private_args(parser)
    args = parser.parse_args()
    warn_deprecated_private_args(args)

    os.umask(0o077)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    id_to_name: dict[str, str] = {}
    agents_root = AGENT_DATA / "agents"
    dest_agents, dest_groups, dest_shared = DEST / "01-assistants", DEST / "02-groups", DEST / "00-shared"

    for d in sorted(agents_root.iterdir()):
        if d.is_dir() and (d / "profile.json").exists() and not (d / "group.json").exists():
            export_agent(d, dest_agents, id_to_name)
    for d in sorted(agents_root.iterdir()):
        if d.is_dir() and (d / "group.json").exists():
            export_group(d, dest_groups, id_to_name)
    export_shared(dest_shared)
    mem_status = apply_memory_drop(DEST, id_to_name)

    # ---- full conversations from the platform-synced search index
    def layout(info):
        base = dest_groups if info["kind"] == "group" else dest_agents
        return base / info["folder"] / "conversations"

    def attach_link_for(info, conv_dir):
        if info.get("dir") is None or info["kind"] == "group":
            return None
        return lambda f: f"../attachments-and-work/{f}"

    conv_stats = conversations.export_all(
        layout, attach_link_for, DEST / "conversation-index.md",
        lambda info, d: str(d.relative_to(DEST)), DEST / "99-backup-program" / "conversation-stats.json",
        rel_root=DEST,
    )
    # ---- copy of this program (repo at HEAD + commit hash)
    prog = snapshot_program(DEST / "99-backup-program")

    roster = ["# 花名冊"] + [f"- {n}（`{i}`）" for i, n in sorted(id_to_name.items(), key=lambda x: x[1])]
    write(DEST / "roster.md", "\n".join(roster))

    total_msgs = sum(s["messages"] for s in conv_stats)
    write(DEST / "README.md", f"""# Virtual Office 可讀匯出包

- 匯出時間（UTC）：`{STAMP}`
- 目的：**給人看、可搬到任何 virtual office**，不是 Grok Bot 內部還原檔
- 人類可讀整理包並非原始資料的逐位元副本；完整原始資料請搭配 `grok-bot-export` 檢查
- 全量備份：所有筆記、附件、對話都包含在內（2026-10-09 起沒有private規則）
- 對話：{len([s for s in conv_stats if s['messages']])} 位助理／群組、共 {total_msgs} 則（來源：平台同步的 search-index.db，含 2026-10-06 之後）
- 備份程式版本：commit `{prog.get('commit')}`（完整原始碼在 `99-backup-program/source/`）

## 結構

- `conversation-index.md` — **所有助理的對話總覽**（則數、期間、連結）
- `00-shared/` — company知識、共用記憶、技能、工作產出
- `01-assistants/<名>/` — 人物設定、avatar、記憶、**conversations/（每月 .md）**、筆記、附件
- `02-groups/<名>/` — 群組設定與群內對話
- `99-backup-program/` — 產生本包的程式原始碼與 commit hash、對話統計
- `roster.md`

## 搜尋

解壓後：`grep -rn "關鍵字" 01-assistants/*/conversations/`

## 已知限制

- 記憶 phase 1：若 `/workspace/exports/memory-drop/` 有投放，會寫進 `00-shared/記憶/` 與各助理 `記憶/platform-memory.md`（見 `memory-index.md`）。沒投放的標「not exported」。本機記憶檔仍停在 2026-10-06 15:10。
- 平台索引只有雙方看得到的訊息文字；工具呼叫／結果只在「補充-frozen local transcript」（約到 2026-09-16）。
- 群組對話中，助理訊息無法區分是哪位助理說的。
""")

    lock_down(DEST)
    archive = OUT_ROOT / f"virtual-office-{STAMP}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(DEST, arcname=DEST.name)
    archive.chmod(0o600)

    files = [p for p in DEST.rglob("*") if p.is_file()]
    print(f"DEST={DEST}")
    print(f"ARCHIVE={archive}")
    print(f"FILES={len(files)}")
    print(f"BYTES={sum(p.stat().st_size for p in files)}")
    print(f"ARCHIVE_BYTES={archive.stat().st_size}")
    print(f"PROGRAM_COMMIT={prog.get('commit')} DIRTY={prog.get('dirty')}")
    print(f"CONVERSATION_MESSAGES={total_msgs}")
    su = mem_status.get("shared_user")
    print("MEMORY_SHARED=" + ("MISSING" if su is None else str(su["count"])))
    for a in mem_status["agents"]:
        flag = "yes" if a["dropped"] else "not exported"
        print(f"MEMORY_AGENT\t{flag}\t{a['id']}\t{a['name']}")
    for name in sorted(id_to_name.values()):
        print(f"AGENT\t{name}")


if __name__ == "__main__":
    main()
