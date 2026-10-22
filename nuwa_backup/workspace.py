"""Copy workspace data with an explicit optional large-directory policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil


def bulky(name: str) -> bool:
    return name.startswith('.venv') or name in {'.cache', 'node_modules', '__pycache__', '.git'}


def enabled() -> bool:
    value = os.environ.get('INCLUDE_BULKY_DIRS', '0')
    if value not in ('0', '1'):
        raise ValueError('INCLUDE_BULKY_DIRS must be 0 or 1')
    return value == '1'


def copy_workspace(src: Path, dst: Path, out_root: Path) -> dict:
    """Preserve every selected file/symlink; do not recursively copy our own outputs."""
    include = enabled()
    src = src.resolve()
    output = out_root.resolve()
    skipped: list[str] = []
    if not src.is_dir():
        return {'include_bulky_dirs': include, 'skipped': [f'{src} (not found)']}
    if output == src:
        raise ValueError('OUT_ROOT cannot equal WORKSPACE')

    def ignored(parent: str, names: list[str]) -> set[str]:
        omissions = set()
        for name in names:
            path = Path(parent) / name
            # Never recurse into the directory where an export is being written.
            if path.resolve() == output:
                omissions.add(name)
                skipped.append(str(path.relative_to(src)) + ' (backup output)')
            elif not include and bulky(name) and path.is_dir():
                omissions.add(name)
                skipped.append(str(path.relative_to(src)) + ' (bulky directory)')
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
        else:
            shutil.copy2(entry, target)
    return {'include_bulky_dirs': include, 'skipped': sorted(skipped)}


if __name__ == '__main__':
    import sys
    if len(sys.argv) != 5:
        raise SystemExit('usage: python -m nuwa_backup.workspace <src> <dst> <out_root> <report.json>')
    result = copy_workspace(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    report = Path(sys.argv[4]); report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"WORKSPACE_BULKY={'included' if result['include_bulky_dirs'] else 'excluded'}; SKIPPED={len(result['skipped'])}")
