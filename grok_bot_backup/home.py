"""Copy the box user's home directory (default ``/home/box``) into a backup.

Files kept outside WORKSPACE and AGENT_DATA, such as credential folders
(``.secrets``), tool settings (``.config``, ``.gitconfig``, ``.mcp-auth``) and
folders that assistants create for themselves, would otherwise be lost. The
whole home directory is copied by default, **including passwords and tokens**.

Always omitted (no duplicates, no recursion):
  * AGENT_DATA and its resolved target (already in the backup),
  * WORKSPACE if it is inside the home directory (already in the backup),
  * OUT_ROOT, WORKSPACE/exports and earlier grok-bot-export-* / virtual-office-*.

Omitted by default because they can be rebuilt (see DEFAULT_EXCLUDES); put a
name in HOME_INCLUDE_PATHS to keep it. Folders matching the workspace "bulky"
rule (.venv*, .cache, node_modules, __pycache__, .git) are omitted unless
INCLUDE_BULKY_DIRS=1.

Environment:
  INCLUDE_HOME=1|0            copy the home directory (default 1)
  BOX_HOME=/home/box          home directory to copy
  HOME_EXCLUDE_PATHS=a:b      extra paths to omit (relative to BOX_HOME or absolute)
  HOME_INCLUDE_PATHS=a:b      default exclusions to keep after all (e.g. chrome-profile)

Symbolic links are copied as links and never followed. Unreadable files and
folders are listed in the report and skipped; they do not stop the backup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

from .workspace import bulky, enabled as bulky_enabled, generated_backup_artifact

DEFAULT_HOME = "/home/box"
DEFAULT_EXCLUDES: dict[str, str] = {
    ".cache": "cache folder (rebuilt automatically)",
    ".npm": "npm package cache (rebuilt automatically)",
    ".local/lib": "installed Python packages (reinstallable)",
    ".local/include": "installed package headers (reinstallable)",
    "chrome-profile": "browser profile (large; browser cache and machine-bound sessions)",
}


def include_home() -> bool:
    value = os.environ.get("INCLUDE_HOME", "1")
    if value not in ("0", "1"):
        raise ValueError("INCLUDE_HOME must be 0 or 1")
    return value == "1"


def box_home() -> Path:
    return Path(os.environ.get("BOX_HOME") or DEFAULT_HOME).expanduser()


def _paths(variable: str) -> list[str]:
    return [v for v in os.environ.get(variable, "").split(os.pathsep) if v.strip()]


def _relative_or_absolute(home: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    return (raw if raw.is_absolute() else home / raw).resolve()


def home_exclusions(home: Path, out_root: Path, workspace: Path, agent_data: Path,
                    ) -> tuple[dict[Path, str], dict[str, str], list[str]]:
    """Return (resolved roots -> reason, active default excludes, re-included names)."""
    home = home.resolve()
    roots: dict[Path, str] = {}
    for path, reason in [
        (agent_data, "already backed up as agent data (AGENT_DATA)"),
        (workspace, "already backed up as workspace (WORKSPACE)"),
        (workspace / "exports", "default backup directory"),
        (out_root, "backup output (OUT_ROOT)"),
    ]:
        roots.setdefault(Path(path).expanduser().resolve(), reason)

    keep = {Path(v).as_posix().strip("/") for v in _paths("HOME_INCLUDE_PATHS")}
    unknown = sorted(keep - set(DEFAULT_EXCLUDES))
    if unknown:
        raise ValueError("HOME_INCLUDE_PATHS only re-includes default exclusions "
                         f"({', '.join(sorted(DEFAULT_EXCLUDES))}); unknown: {', '.join(unknown)}")
    defaults = {name: why for name, why in DEFAULT_EXCLUDES.items() if name not in keep}
    for name, why in defaults.items():
        roots.setdefault((home / name).resolve(), "default exclusion: " + why)

    for value in _paths("HOME_EXCLUDE_PATHS"):
        target = _relative_or_absolute(home, value)
        if target == home or target in home.parents:
            raise ValueError(f"Refusing to exclude the entire home directory: {target}")
        roots.setdefault(target, "configured exclusion (HOME_EXCLUDE_PATHS)")
    for value in _paths("BACKUP_EXCLUDE_PATHS"):
        if Path(value).expanduser().is_absolute():
            target = Path(value).expanduser().resolve()
            if target != home and target not in home.parents:
                roots.setdefault(target, "configured exclusion (BACKUP_EXCLUDE_PATHS)")
    return roots, defaults, sorted(keep)


def _tree_bytes(path: Path) -> int:
    """Apparent size without following links; unreadable parts count as 0."""
    try:
        if path.is_symlink() or not path.is_dir():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for parent, dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(parent, name)).st_size
            except OSError:
                pass
        for name in dirs:
            if os.path.islink(os.path.join(parent, name)):
                try:
                    total += os.lstat(os.path.join(parent, name)).st_size
                except OSError:
                    pass
    return total


def copy_home(home: Path, dst: Path, out_root: Path, workspace: Path, agent_data: Path) -> dict:
    """Copy `home` into `dst`, returning a JSON-serializable report."""
    report: dict = {
        "enabled": include_home(),
        "home": str(home),
        "include_bulky_dirs": bulky_enabled(),
    }
    if not report["enabled"]:
        report["note"] = "INCLUDE_HOME=0: home directory not copied"
        return report
    if not home.is_dir():
        report["note"] = "home directory not found; nothing copied"
        return report

    src = home.resolve()
    output = out_root.resolve()
    dst = dst.resolve()
    if output == src or output in src.parents:
        raise ValueError(f"OUT_ROOT must not contain the home directory: {output}")
    if dst == src or (src in dst.parents and not (dst == output or output in dst.parents)):
        raise ValueError(f"Destination inside the home directory but outside OUT_ROOT: {dst}")

    roots, defaults, kept = home_exclusions(src, output, workspace, agent_data)
    include_bulky = report["include_bulky_dirs"]
    skipped: list[dict] = []
    unreadable: list[dict] = []
    counts = {"files": 0, "symlinks": 0, "directories": 0, "bytes": 0}
    top: dict[str, dict] = {}

    def rel(path: Path) -> str:
        return path.relative_to(src).as_posix()

    def reason_for(path: Path) -> str | None:
        try:
            target = path.resolve()
        except (OSError, RuntimeError):
            target = path
        for root, why in roots.items():
            if target == root or root in target.parents:
                return why
        # The link path itself may sit at an excluded location even when its
        # target is elsewhere.
        for root, why in roots.items():
            if path == root or root in path.parents:
                return why
        artifact = generated_backup_artifact(path)
        if artifact:
            return artifact
        if not include_bulky and path.is_dir() and not path.is_symlink() and bulky(path.name):
            return "bulky directory (INCLUDE_BULKY_DIRS=0)"
        return None

    def note_top(path: Path, kind: str, size: int) -> None:
        name = rel(path).split("/", 1)[0]
        entry = top.setdefault(name, {"name": name, "files": 0, "symlinks": 0, "bytes": 0})
        if kind == "file":
            entry["files"] += 1
            entry["bytes"] += size
        elif kind == "link":
            entry["symlinks"] += 1

    def copy_entry(path: Path, target: Path) -> None:
        why = reason_for(path)
        if why is not None:
            skipped.append({"path": rel(path), "reason": why, "bytes": _tree_bytes(path)})
            return
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            unreadable.append({"path": rel(path), "error": exc.strerror or str(exc)})
            return
        if stat.S_ISLNK(mode):
            os.symlink(os.readlink(path), target)
            counts["symlinks"] += 1
            note_top(path, "link", 0)
        elif stat.S_ISDIR(mode):
            try:
                children = sorted(os.scandir(path), key=lambda e: e.name)
            except OSError as exc:
                unreadable.append({"path": rel(path) + "/", "error": exc.strerror or str(exc)})
                return
            target.mkdir(exist_ok=True)
            counts["directories"] += 1
            top.setdefault(rel(path).split("/", 1)[0],
                           {"name": rel(path).split("/", 1)[0], "files": 0, "symlinks": 0, "bytes": 0})
            for child in children:
                copy_entry(Path(child.path), target / child.name)
            try:
                shutil.copystat(path, target, follow_symlinks=False)
            except OSError:
                pass
        elif stat.S_ISREG(mode):
            try:
                shutil.copy2(path, target, follow_symlinks=False)
            except OSError as exc:
                if target.exists():
                    target.unlink()
                unreadable.append({"path": rel(path), "error": exc.strerror or str(exc)})
                return
            size = target.stat().st_size
            counts["files"] += 1
            counts["bytes"] += size
            note_top(path, "file", size)
        else:
            skipped.append({"path": rel(path), "reason": "special file (socket, pipe or device)",
                            "bytes": 0})

    dst.mkdir(parents=True, exist_ok=True)
    try:
        children = sorted(os.scandir(src), key=lambda e: e.name)
    except OSError as exc:
        raise PermissionError(f"Cannot list home directory {src}: {exc}") from exc
    for child in children:
        copy_entry(Path(child.path), dst / child.name)

    report.update({
        "home_resolved": str(src),
        "default_exclusions": defaults,
        "reincluded_defaults": kept,
        "excluded_roots": [{"path": str(p), "reason": why} for p, why in sorted(roots.items())],
        "included_top_level": sorted(top.values(), key=lambda e: e["name"]),
        "skipped": sorted(skipped, key=lambda e: e["path"]),
        "unreadable": sorted(unreadable, key=lambda e: e["path"]),
        **counts,
    })
    return report


def main() -> None:
    import sys
    from .common import AGENT_DATA, WORKSPACE
    if len(sys.argv) != 4:
        raise SystemExit("usage: python -m grok_bot_backup.home <dst> <out_root> <report.json>")
    dst, out_root, report_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    result = copy_home(box_home(), dst, out_root, WORKSPACE, AGENT_DATA)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["enabled"] or "note" in result:
        print(f"HOME={result['home']}; {result.get('note', 'disabled')}")
        return
    print(f"HOME={result['home']}; FILES={result['files']}; SYMLINKS={result['symlinks']}; "
          f"BYTES={result['bytes']}; SKIPPED={len(result['skipped'])}; "
          f"UNREADABLE={len(result['unreadable'])}")


if __name__ == "__main__":
    main()
