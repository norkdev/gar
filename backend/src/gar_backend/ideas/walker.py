"""Folder walker for the private idea source.

Honors:
- `.gitignore` at the walk root (nested `.gitignore` files are NOT consulted in v1)
- `.ignore` at the walk root (same syntax as `.gitignore`; spec §8)
- Implicit exclusions: `.obsidian/` (Obsidian system folder), `.git/`
- File-type filter: Markdown only (`.md`)
- Symlinks are not followed (avoid loops and out-of-vault leakage)

PDF and image support is Future Work and will extend the file-type filter
and the reader together.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pathspec

MARKDOWN_SUFFIX = ".md"
SYSTEM_FOLDERS = frozenset({".obsidian", ".git"})
IGNORE_FILES = (".gitignore", ".ignore")


def walk(root: Path) -> Iterator[Path]:
    """Yield Markdown files under `root` under v1 filtering rules.

    If `root` is a single file, yield it iff it is a Markdown file.
    Raises FileNotFoundError if `root` does not exist.
    """
    if not root.exists():
        raise FileNotFoundError(root)

    if root.is_file():
        if root.suffix == MARKDOWN_SUFFIX:
            yield root
        return

    spec = _load_ignore_spec(root)
    yield from _walk_dir(root, root, spec)


def _load_ignore_spec(root: Path) -> pathspec.PathSpec:
    patterns: list[str] = []
    for filename in IGNORE_FILES:
        path = root / filename
        if path.is_file():
            patterns.extend(path.read_text(encoding="utf-8").splitlines())
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _walk_dir(current: Path, root: Path, spec: pathspec.PathSpec) -> Iterator[Path]:
    for entry in sorted(current.iterdir()):
        if entry.is_symlink():
            continue
        if entry.name in SYSTEM_FOLDERS:
            continue

        relative_posix = entry.relative_to(root).as_posix()

        if entry.is_dir():
            # Trailing slash so directory-only gitignore patterns match.
            if spec.match_file(relative_posix + "/"):
                continue
            yield from _walk_dir(entry, root, spec)
        elif entry.is_file():
            if spec.match_file(relative_posix):
                continue
            if entry.suffix == MARKDOWN_SUFFIX:
                yield entry
