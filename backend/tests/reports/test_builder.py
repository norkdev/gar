"""reports/builder integration tests (write + naming + .ignore)."""

from datetime import date
from pathlib import Path

from gar_backend.reports.builder import save_report


def test_save_report_writes_content_to_dated_filename(tmp_path: Path) -> None:
    path = save_report(
        content="# Report body",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    assert path == tmp_path / "gar-report-2026-05-25.md"
    assert path.read_text() == "# Report body"


def test_save_report_appends_to_ignore(tmp_path: Path) -> None:
    save_report(
        content="x",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    ignore = (tmp_path / ".ignore").read_text()
    assert "gar-report-2026-05-25.md" in ignore


def test_save_report_with_file_vault_saves_in_parent(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("idea")
    path = save_report(
        content="x",
        vault_path=f,
        today=date(2026, 5, 25),
    )
    assert path.parent == tmp_path
    assert path.name == "gar-report-2026-05-25.md"


def test_save_report_same_day_twice_uses_suffix(tmp_path: Path) -> None:
    save_report(
        content="first",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    second = save_report(
        content="second",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    assert second.name == "gar-report-2026-05-25-2.md"
    assert second.read_text() == "second"


def test_save_report_does_not_overwrite_existing(tmp_path: Path) -> None:
    first = save_report(
        content="first",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    save_report(
        content="second",
        vault_path=tmp_path,
        today=date(2026, 5, 25),
    )
    assert first.read_text() == "first"  # original preserved


def test_save_report_records_both_files_in_ignore(tmp_path: Path) -> None:
    save_report(content="x", vault_path=tmp_path, today=date(2026, 5, 25))
    save_report(content="y", vault_path=tmp_path, today=date(2026, 5, 25))
    lines = (tmp_path / ".ignore").read_text().splitlines()
    assert "gar-report-2026-05-25.md" in lines
    assert "gar-report-2026-05-25-2.md" in lines


def test_save_report_default_date_is_today(tmp_path: Path) -> None:
    """Smoke test that the default `today` parameter resolves without crashing."""
    path = save_report(content="x", vault_path=tmp_path)
    # Filename should follow `gar-report-YYYY-MM-DD.md` pattern
    assert path.name.startswith("gar-report-")
    assert path.name.endswith(".md")
