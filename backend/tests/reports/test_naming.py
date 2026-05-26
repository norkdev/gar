"""reports/naming unit tests."""

from datetime import date
from pathlib import Path

from gar_backend.reports.naming import (
    IGNORE_FILENAME,
    append_to_ignore,
    next_report_filename,
    resolve_save_dir,
)

# ---------- resolve_save_dir ----------


def test_resolve_save_dir_for_folder_is_self(tmp_path: Path) -> None:
    assert resolve_save_dir(tmp_path) == tmp_path


def test_resolve_save_dir_for_file_is_parent(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("x")
    assert resolve_save_dir(f) == tmp_path


# ---------- next_report_filename ----------


def test_next_report_filename_first_call_uses_date(tmp_path: Path) -> None:
    assert next_report_filename(tmp_path, date(2026, 5, 25)) == "report-2026-05-25.md"


def test_next_report_filename_second_call_adds_suffix_2(tmp_path: Path) -> None:
    (tmp_path / "report-2026-05-25.md").write_text("existing")
    assert next_report_filename(tmp_path, date(2026, 5, 25)) == "report-2026-05-25-2.md"


def test_next_report_filename_continues_to_increment(tmp_path: Path) -> None:
    for name in [
        "report-2026-05-25.md",
        "report-2026-05-25-2.md",
        "report-2026-05-25-3.md",
    ]:
        (tmp_path / name).write_text("x")
    assert next_report_filename(tmp_path, date(2026, 5, 25)) == "report-2026-05-25-4.md"


def test_next_report_filename_different_day_starts_fresh(tmp_path: Path) -> None:
    (tmp_path / "report-2026-05-24.md").write_text("yesterday")
    assert next_report_filename(tmp_path, date(2026, 5, 25)) == "report-2026-05-25.md"


def test_next_report_filename_ignores_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "random.md").write_text("x")
    (tmp_path / "report-other-prefix.md").write_text("x")
    assert next_report_filename(tmp_path, date(2026, 5, 25)) == "report-2026-05-25.md"


# ---------- append_to_ignore ----------


def test_append_to_ignore_creates_file_if_missing(tmp_path: Path) -> None:
    ignore_path = append_to_ignore(tmp_path, "report-2026-05-25.md")
    assert ignore_path.name == IGNORE_FILENAME
    assert ignore_path.read_text() == "report-2026-05-25.md\n"


def test_append_to_ignore_appends_to_existing_file(tmp_path: Path) -> None:
    (tmp_path / IGNORE_FILENAME).write_text("report-2026-05-24.md\n")
    append_to_ignore(tmp_path, "report-2026-05-25.md")
    lines = (tmp_path / IGNORE_FILENAME).read_text().splitlines()
    assert lines == ["report-2026-05-24.md", "report-2026-05-25.md"]


def test_append_to_ignore_no_duplicate_entries(tmp_path: Path) -> None:
    """Defensive: a partial-run retry shouldn't add the same name twice."""
    (tmp_path / IGNORE_FILENAME).write_text("report-2026-05-25.md\n")
    append_to_ignore(tmp_path, "report-2026-05-25.md")
    lines = (tmp_path / IGNORE_FILENAME).read_text().splitlines()
    assert lines == ["report-2026-05-25.md"]


def test_append_to_ignore_preserves_other_user_entries(tmp_path: Path) -> None:
    """The user (or other tooling) may have added their own entries to .ignore."""
    (tmp_path / IGNORE_FILENAME).write_text("scratch.md\nsecret-notes.md\n")
    append_to_ignore(tmp_path, "report-2026-05-25.md")
    lines = (tmp_path / IGNORE_FILENAME).read_text().splitlines()
    assert lines == ["scratch.md", "secret-notes.md", "report-2026-05-25.md"]


def test_append_to_ignore_returns_ignore_path(tmp_path: Path) -> None:
    path = append_to_ignore(tmp_path, "x.md")
    assert path == tmp_path / IGNORE_FILENAME
