"""Report filename generation and `.ignore` accounting (spec §7, §8).

- The save directory is the vault folder (if vault_path is a folder) or
  its parent (if vault_path is a single file).
- Filenames are ``report-YYYY-MM-DD.md``; same-day reruns get ``-2``,
  ``-3``, etc.
- Past reports are never overwritten.
- Each generated filename is appended to ``.ignore`` in the same directory
  so the walker skips it on subsequent runs.
"""

from datetime import date
from pathlib import Path

REPORT_PREFIX = "report"
IGNORE_FILENAME = ".ignore"


def resolve_save_dir(vault_path: Path) -> Path:
    """The directory where the report will be saved."""
    return vault_path.parent if vault_path.is_file() else vault_path


def next_report_filename(save_dir: Path, today: date) -> str:
    """Return a filename that does not collide with existing files.

    First call on a given day → ``report-YYYY-MM-DD.md``.
    Subsequent same-day calls → ``report-YYYY-MM-DD-2.md``, ``-3.md``, ...
    """
    base = f"{REPORT_PREFIX}-{today.isoformat()}"
    if not (save_dir / f"{base}.md").exists():
        return f"{base}.md"
    n = 2
    while (save_dir / f"{base}-{n}.md").exists():
        n += 1
    return f"{base}-{n}.md"


def append_to_ignore(save_dir: Path, filename: str) -> Path:
    """Append ``filename`` to ``.ignore`` in ``save_dir`` (creating it if missing).

    No-op if ``filename`` is already present, so that a partial-run retry
    won't produce duplicate entries.
    """
    ignore_path = save_dir / IGNORE_FILENAME
    existing: set[str] = set()
    if ignore_path.exists():
        existing = {
            line.strip()
            for line in ignore_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    if filename in existing:
        return ignore_path
    with ignore_path.open("a", encoding="utf-8") as f:
        f.write(f"{filename}\n")
    return ignore_path
