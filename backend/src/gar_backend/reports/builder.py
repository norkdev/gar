"""Save a generated report to disk and update ``.ignore``.

v1 keeps this thin: the LLM (via ``phase_compose_report``) produces the
Markdown text directly per the structure mandated in ``COMPOSE_REPORT_SYSTEM``
(spec §7). This module only chooses the right filename, writes the file,
and records it in ``.ignore`` so the walker excludes it on the next run.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gar_backend.reports.naming import (
    append_to_ignore,
    next_report_filename,
    resolve_save_dir,
)


def save_report(*, content: str, vault_path: Path, today: date | None = None) -> Path:
    """Save ``content`` as a new report under the vault's save directory.

    Returns the path of the saved file. Also appends the filename to
    ``.ignore`` in the same directory.
    """
    save_dir = resolve_save_dir(vault_path)
    today = today or date.today()
    filename = next_report_filename(save_dir, today)
    path = save_dir / filename
    path.write_text(content, encoding="utf-8")
    append_to_ignore(save_dir, filename)
    return path
