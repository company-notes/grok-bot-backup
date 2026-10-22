"""Full chat history for every assistant, from the platform-synced search index.

Source: /home/box/agent-data/search-index.db (tables messages, media, agents).
Since the 2026-10-06 host move the platform no longer writes transcripts back
to agents/<id>/store.db or agent-transcripts/, but it keeps this index current,
so it is the only on-box source of conversations after ~2026-09-16.

It contains the user-visible text of user and assistant messages only
(no tool calls / tool results). The frozen local transcripts (with tool calls,
up to ~2026-09-16) are rendered as a supplement.
"""
from __future__ import annotations

import os

import argparse
import json
import pathlib
import re
import shutil
import sqlite3
import time
from collections import Counter, OrderedDict
from typing import Callable

from .common import (
    AGENT_DATA, SEARCH_INDEX, WEEKDAYS, agent_registry, fmt_taipei, lock_down,
    load_json, taipei, unknown_agent_entry, write,
)

USER_LABEL = os.environ.get("NUWA_BACKUP_USER_LABEL", "使用者")
GROUP_ASSISTANT_LABEL = "助理（群組內，索引未標示是哪位）"
ATTACH_RE = re.compile(r"attachments/([0-9a-f]{64})")


# --------------------------------------------------------------------------- source
def open_index_snapshot(path: pathlib.Path = SEARCH_INDEX) -> tuple[sqlite3.Connection, int]:
    """Consistent in-memory copy of the live index (includes WAL content)."""
    if not path.exists():
        raise SystemExit(f"ERROR: search index not found: {path}")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    return mem, int(time.time() * 1000)


def index_agent_ids(db: sqlite3.Connection) -> list[str]:
    ids = {r[0] for r in db.execute("select agent_id from agents")}
    ids |= {r[0] for r in db.execute("select distinct agent_id from messages")}
    return sorted(ids)


def fetch_messages(db, agent_id):
    return db.execute(
        "select seq, entry_id, role, timestamp_ms, body from messages "
        "where agent_id=? order by timestamp_ms, seq, id",
        (agent_id,),
    ).fetchall()


def fetch_media(db, agent_id):
    cols = [r[1] for r in db.execute("pragma table_info(media)")]
    rows = db.execute(
        "select * from media where agent_id=? order by timestamp_ms, seq, id", (agent_id,)
    ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


# --------------------------------------------------------------------------- attachments
def attachment_map(agent_dir: pathlib.Path | None, media: list[dict]) -> dict[str, tuple[str, str]]:
    """entry_id -> (attachment file name on box, 'exact'|'guess')."""
    if agent_dir is None:
        return {}
    att = agent_dir / "attachments"
    files = {}
    if att.exists():
        for f in att.iterdir():
            if f.is_file():
                files[f.name.split(".")[0]] = f.name
    out: dict[str, tuple[str, str]] = {}
    store = agent_dir / "store.db"
    if store.exists():
        try:
            c = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
            for eid, entry in c.execute("select id, entry from transcript_entries"):
                for h in ATTACH_RE.findall(entry):
                    if h in files and eid not in out:
                        out[eid] = (files[h], "exact")
            c.close()
        except sqlite3.Error:
            pass
    # Fallback: if exactly one unmatched file has the same extension, call it a guess.
    used = {v[0] for v in out.values()}
    unmatched = [f for f in files.values() if f not in used]
    for m in media:
        if m["entry_id"] in out:
            continue
        ext = (m.get("ext") or "").lower()
        cands = [f for f in unmatched if ext and f.lower().endswith(ext)]
        if len(cands) == 1:
            out[m["entry_id"]] = (cands[0], "guess")
            unmatched.remove(cands[0])
    return out


# --------------------------------------------------------------------------- rendering
def cell(text: str) -> str:
    """Escape a value for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def speaker(role: str, info: dict) -> str:
    if role == "user":
        return USER_LABEL
    if info["kind"] == "group":
        return GROUP_ASSISTANT_LABEL
    return info["short"]


def render_month(info, month, rows, media_by_entry) -> str:
    users = sum(1 for r in rows if r[2] == "user")
    out = [
        f"# {info['name']}｜對話紀錄 {month}",
        "",
        f"- 時區：Asia/Taipei（UTC+8）",
        f"- 本月訊息：{len(rows)} 則（{USER_LABEL} {users}／助理 {len(rows) - users}）",
        f"- 來源：平台同步的 search-index.db；訊息內文原文照錄，未經修改",
        "",
    ]
    cur_day = None
    for seq, eid, role, ts, body in rows:
        dt = taipei(ts)
        day = dt.strftime("%Y-%m-%d") if dt else "未知日期"
        if day != cur_day:
            cur_day = day
            wd = f"（{WEEKDAYS[dt.weekday()]}）" if dt else ""
            out += ["---", "", f"## {day}{wd}", ""]
        out.append(f"<!-- entry:{eid} seq:{seq} -->")
        out.append(f"### {fmt_taipei(ts)} · {speaker(role, info)}")
        out.append("")
        out.append(body if body is not None else "")
        att = media_by_entry.get(eid)
        if att:
            out.append("")
            out.append(f"> 📎 附件：{att['file_name']}（見 `附件清單.md`）")
        out.append("")
    return "\n".join(out) + "\n"


def render_frozen_transcript(jsonl: pathlib.Path, info, result_limit: int = 2000) -> str:
    out = [
        f"# {info['name']}｜本機凍結逐字稿（含工具呼叫）",
        "",
        "- 來源：`agent-transcripts/<id>/<id>.jsonl`（2026-10-06 後不再更新，內容約到 2026-09-16）",
        "- 這份沒有時間戳；完整原始資料見同資料夾的 `agent-transcript.jsonl`",
        f"- 工具結果超過 {result_limit} 字會截斷（原始 jsonl 未截斷）",
        "",
    ]
    with jsonl.open(encoding="utf-8", errors="replace") as f:
        for n, raw in enumerate(f, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            role = row.get("role", "?")
            for b in (row.get("message") or {}).get("content") or []:
                t = b.get("type")
                if t == "text":
                    label = {"user": "輸入（使用者／系統）", "assistant": "助理（內部文字）"}.get(role, role)
                    out += [f"### #{n} {label}", "", b.get("text", ""), ""]
                elif t == "tool_use":
                    inp = b.get("input")
                    inp = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False, indent=1)
                    out += [f"### #{n} 工具呼叫：`{b.get('name')}`", "", "```", inp, "```", ""]
                elif t == "tool_result":
                    res = b.get("result")
                    res = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
                    if len(res) > result_limit:
                        res = res[:result_limit] + f"\n…（截斷，原長 {len(res)} 字）"
                    out += [f"#### #{n} 工具結果：`{b.get('name')}`", "", "```", res, "```", ""]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- export
def export_agent_conversation(db, info, conv_dir: pathlib.Path, attach_link: Callable[[str], str] | None,
                              cutoff_ms: int) -> dict:
    rows = fetch_messages(db, info["id"])
    media = fetch_media(db, info["id"])
    amap = attachment_map(info.get("dir"), media)
    media_by_entry = {m["entry_id"]: m for m in media}
    conv_dir.mkdir(parents=True, exist_ok=True)

    months: "OrderedDict[str, list]" = OrderedDict()
    for r in rows:
        dt = taipei(r[3])
        months.setdefault(dt.strftime("%Y-%m") if dt else "unknown", []).append(r)
    for month, mrows in months.items():
        write(conv_dir / f"{month}.md", render_month(info, month, mrows, media_by_entry))

    # raw machine-readable copies
    raw = conv_dir / "raw"
    raw.mkdir(exist_ok=True)
    with (raw / "messages.jsonl").open("w", encoding="utf-8") as f:
        for seq, eid, role, ts, body in rows:
            f.write(json.dumps({"seq": seq, "entry_id": eid, "role": role, "timestamp_ms": ts,
                                "time_taipei": fmt_taipei(ts), "body": body}, ensure_ascii=False) + "\n")
    with (raw / "media.jsonl").open("w", encoding="utf-8") as f:
        for m in media:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # attachments list
    lines = [f"# {info['name']}｜附件清單", "",
             "| 時間（台北） | 檔名 | 類型 | 方向 | 本機檔案 |", "|---|---|---|---|---|"]
    found = 0
    for m in media:
        eid = m["entry_id"]
        direction = "使用者上傳" if re.fullmatch(r"t\d+ua\d+", eid) else "助理傳送"
        hit = amap.get(eid)
        if hit and attach_link:
            found += 1
            mark = "" if hit[1] == "exact" else "（依副檔名推測）"
            loc = f"[`{hit[0]}`](<{attach_link(hit[0])}>){mark}"
        elif hit:
            found += 1
            loc = f"`{hit[0]}`"
        else:
            loc = "（box 上找不到對應檔案）"
        lines.append(f"| {fmt_taipei(m['timestamp_ms'], False)} | {cell(m['file_name'])} | {m.get('kind')} | {direction} | {loc} |")
    if not media:
        lines.append("| — | （無附件） | | | |")
    box_files = []
    if info.get("dir") and (info["dir"] / "attachments").exists():
        box_files = sorted(p.name for p in (info["dir"] / "attachments").iterdir() if p.is_file())
    mapped = {v[0] for v in amap.values()}
    orphan = [f for f in box_files if f not in mapped]
    if orphan:
        lines += ["", "## box 上其他附件檔（對不到訊息）", ""]
        lines += [f"- [`{f}`](<{attach_link(f)}>)" if attach_link else f"- `{f}`" for f in orphan]
    lines += ["", "> 附件檔本身以 hash 檔名存放；連結指向本備份內的實際檔案位置。"]
    write(conv_dir / "附件清單.md", "\n".join(lines))

    # supplement: frozen local transcript with tool calls
    tj = AGENT_DATA / "agent-transcripts" / info["id"] / f"{info['id']}.jsonl"
    has_frozen = tj.exists()
    if has_frozen:
        sup = conv_dir / "補充-本機凍結逐字稿-含工具呼叫"
        sup.mkdir(exist_ok=True)
        shutil.copy2(tj, sup / "agent-transcript.jsonl")
        write(sup / "逐字稿.md", render_frozen_transcript(tj, info))

    users = sum(1 for r in rows if r[2] == "user")
    stats = {
        "agent_id": info["id"], "name": info["name"], "kind": info["kind"],
        "messages": len(rows), "user": users, "assistant": len(rows) - users,
        "first": fmt_taipei(rows[0][3]) if rows else None,
        "last": fmt_taipei(rows[-1][3]) if rows else None,
        "last_ms": rows[-1][3] if rows else None,
        "months": {k: len(v) for k, v in months.items()},
        "media": len(media), "media_files_found": found,
        "frozen_supplement": has_frozen, "cutoff_ms": cutoff_ms,
    }

    # per-agent index
    idx = [f"# {info['name']}｜對話紀錄索引", "",
           f"- 內部 ID：`{info['id']}`",
           f"- 訊息總數：**{len(rows)}** 則（{USER_LABEL} {users}／助理 {len(rows) - users}）",
           f"- 期間（台北時間）：{stats['first'] or '—'} ～ {stats['last'] or '—'}",
           f"- 資料擷取時間（台北）：{fmt_taipei(cutoff_ms)}", ""]
    if rows:
        idx += ["## 每月檔案", "", "| 月份 | 則數 | 檔案 |", "|---|---|---|"]
        idx += [f"| {k} | {v} | [`{k}.md`](<{k}.md>) |" for k, v in stats["months"].items()]
    else:
        idx += ["_此助理在平台索引中沒有任何訊息。_"]
    idx += ["", "## 其他", "",
            f"- [`附件清單.md`](附件清單.md)：{len(media)} 個附件（box 上找到 {found} 個）",
            "- `raw/messages.jsonl`、`raw/media.jsonl`：機器可讀原始資料"]
    if has_frozen:
        idx.append("- `補充-本機凍結逐字稿-含工具呼叫/`：舊的本機逐字稿（含工具呼叫，無時間戳，約到 2026-09-16）")
    idx += ["", "## 說明", "",
            f"- 「{USER_LABEL}」是對話中使用者一方的訊息，通常是 owner，也可能是其他人或其他助理轉來的訊息。",
            "- 只含雙方看得到的訊息文字；工具呼叫與工具結果不在平台索引內。",
            "- 搜尋範例：`grep -rn \"關鍵字\" .`"]
    write(conv_dir / "README.md", "\n".join(idx))
    return stats


def write_top_index(path: pathlib.Path, stats: list[dict], links: dict[str, str], cutoff_ms: int,
                    skipped: list[dict]) -> None:
    total = sum(s["messages"] for s in stats)
    lines = ["# 全部助理對話索引", "",
             f"- 資料擷取時間（台北）：{fmt_taipei(cutoff_ms)}",
             f"- 來源：`search-index.db`（平台同步，含 2026-10-06 之後的對話）",
             f"- 訊息總數：**{total}** 則", "",
             "| 助理／群組 | 類型 | 則數 | 期間（台北） | 附件 | 資料夾 |", "|---|---|---|---|---|---|"]
    kind_label = {"agent": "助理", "group": "群組", "deleted": "已刪除", "unknown": "未知"}
    for s in sorted(stats, key=lambda x: (-x["messages"], x["name"])):
        if s["messages"] == 0:
            continue
        link = links[s["agent_id"]]
        lines.append(f"| {cell(s['name'])} | {kind_label.get(s['kind'], s['kind'])} | {s['messages']} | "
                     f"{s['first']} ～ {s['last']} | {s['media']} | [{cell(link)}](<{link}/README.md>) |")
    empty = [s for s in stats if s["messages"] == 0] + skipped
    if empty:
        lines += ["", "## 沒有訊息（略過內容）", ""]
        lines += [f"- {s['name']}（`{s['agent_id']}`）" for s in empty]
    deleted = load_json(AGENT_DATA / "deleted-agents.json", {}) or {}
    dids = list((deleted.get("agents") or {}).keys()) if isinstance(deleted, dict) else []
    if dids:
        present = {s["agent_id"] for s in stats}
        lines += ["", "## 已刪除的助理", "",
                  "以下助理已在平台刪除（`deleted-agents.json`）。若索引裡還有它們的訊息，會以「（已刪除助理）」資料夾匯出；目前狀態："]
        lines += [f"- `{d}`：{'已匯出' if d in present else '索引中無資料（無可備份內容）'}" for d in dids]
    lines += ["", "## 搜尋", "", "解壓後可直接用 `grep -rn \"關鍵字\" .` 搜尋所有對話。"]
    write(path, "\n".join(lines))


def export_all(layout: Callable[[dict], pathlib.Path], attach_link_for: Callable[[dict, pathlib.Path], Callable | None],
               index_path: pathlib.Path, link_for: Callable[[dict, pathlib.Path], str],
               stats_path: pathlib.Path, rel_root: pathlib.Path | None = None) -> list[dict]:
    db, cutoff = open_index_snapshot()
    reg = agent_registry()
    ids = sorted(set(index_agent_ids(db)) | set(reg))
    stats, links = [], {}
    for aid in ids:
        info = reg.get(aid) or unknown_agent_entry(aid)
        conv_dir = layout(info)
        s = export_agent_conversation(db, info, conv_dir, attach_link_for(info, conv_dir), cutoff)
        s["conv_dir"] = str(conv_dir.relative_to(rel_root)) if rel_root else str(conv_dir)
        stats.append(s)
        links[aid] = link_for(info, conv_dir)
    write_top_index(index_path, stats, links, cutoff, [])
    write(stats_path, json.dumps({"cutoff_ms": cutoff, "cutoff_taipei": fmt_taipei(cutoff),
                                  "source": str(SEARCH_INDEX), "agents": stats},
                                 ensure_ascii=False, indent=1))
    db.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Export all assistants' conversations as Markdown")
    ap.add_argument("--dest", required=True, help="output folder (one sub-folder per assistant)")
    args = ap.parse_args()
    dest = pathlib.Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    def layout(info):
        prefix = {"group": "群組-", "deleted": "", "unknown": ""}.get(info["kind"], "")
        return dest / f"{prefix}{info['folder']}"

    def attach_link_for(info, conv_dir):
        if info.get("dir") is None:
            return None
        return lambda f: f"../../agents/{info['id']}/attachments/{f}"

    stats = export_all(layout, attach_link_for, dest / "README.md",
                       lambda info, d: d.name, dest / "conversation-stats.json", rel_root=dest.parent)
    lock_down(dest)
    print(f"CONVERSATIONS_DIR={dest}")
    for s in stats:
        print(f"CONV\t{s['messages']}\t{s['name']}")


if __name__ == "__main__":
    main()
