"""Copy workspace files without ever copying backup output into itself.

All accessible files (including secrets) are included by default. Operators can
exclude arbitrary paths with BACKUP_EXCLUDE_PATHS, separated by os.pathsep (: on
Linux). The active OUT_ROOT and WORKSPACE/exports are always excluded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil


def bulky(name: str) -> bool:
    return name.startswith(".venv") or name in {".cache", "node_modules", "__pycache__", ".git"}


def enabled() -> bool:
    value = os.environ.get("INCLUDE_BULKY_DIRS", "0")
    if value not in ("0", "1"):
        raise ValueError("INCLUDE_BULKY_DIRS must be 0 or 1")
    return value == "1"


def exclusions(workspace: Path, out_root: Path) -> dict[Path, str]:
    """Resolve configured exclusions without following links during copying."""
    workspace = workspace.resolve()
    targets = {
        (workspace / "exports").resolve(): "default backup directory",
        out_root.resolve(): "backup output",
    }
    for value in filter(None, os.environ.get("BACKUP_EXCLUDE_PATHS", "").split(os.pathsep)):
        raw = Path(value).expanduser()
        target = (raw if raw.is_absolute() else workspace / raw).resolve()
        if target == workspace or target in workspace.parents:
            raise ValueError(f"Refusing to exclude the entire workspace: {target}")
        targets[target] = "configured exclusion"
    return targets


def copy_workspace(src: Path, dst: Path, out_root: Path) -> dict:
    """Copy selected files safely and report each deliberately skipped path.

    Refuses unsafe output locations before making any copy. Paths are compared
    by resolved absolute location, preventing recursion through nested folders
    or symbolic links. Symlinks in included trees are preserved, not followed.
    """
    include = enabled()
    src = src.resolve()
    output = out_root.resolve()
    dst = dst.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"WORKSPACE not found: {src}")
    if output == src or output in src.parents:
        raise ValueError(f"OUT_ROOT must not contain WORKSPACE: {output}")
    if dst == src or (src in dst.parents and not (dst == output or output in dst.parents)):
        raise ValueError(f"Destination inside WORKSPACE but outside OUT_ROOT: {dst}")

    excluded = exclusions(src, output)
    skipped: list[str] = []

    def ignored(parent: str, names: list[str]) -> set[str]:
        omissions = set()
        for name in names:
            path = Path(parent) / name
            target = path.resolve()
            reason = next((why for root, why in excluded.items()
                           if target == root or root in target.parents), None)
            if reason is None and not include and path.is_dir() and bulky(name):
                reason = "bulky directory"
            if reason is not None:
                omissions.add(name)
                skipped.append(f"{path.relative_to(src)} ({reason})")
        return omissions

    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name in ignored(str(src), [entry.name]):
            continue
        target = dst / entry.name
        if entry.is_symlink():
            target.symlink_to(os.readlink(entry))
        elif entry.is_dir():
            shutil.copytree(entry, target, ignore=ignored, symlinks=True, dirs_exist_ok=True)
        elif entry.is_file():
            shutil.copy2(entry, target)
    return {
        "include_bulky_dirs": include,
        "excluded_roots": [str(p) for p in sorted(excluded)],
        "skipped": sorted(set(skipped)),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 5:
        raise SystemExit("usage: python -m nuwa_backup.workspace <src> <dst> <out_root> <report.json>")
    result = copy_workspace(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    report = Path(sys.argv[4])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WORKSPACE_BULKY={'included' if result['include_bulky_dirs'] else 'excluded'}; SKIPPED={len(result['skipped'])}")
