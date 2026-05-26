"""ideas/walker unit tests. Uses tmp_path for filesystem isolation."""

from pathlib import Path

import pytest
from gar_backend.ideas.walker import walk


def test_walk_single_md_file_yields_it(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("hello")
    assert list(walk(f)) == [f]


def test_walk_single_non_md_file_yields_nothing(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("hello")
    assert list(walk(f)) == []


def test_walk_nonexistent_path_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(walk(tmp_path / "nope"))


def test_walk_folder_returns_only_md_files(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.md").write_text("c")
    names = sorted(p.name for p in walk(tmp_path))
    assert names == ["a.md", "c.md"]


def test_walk_recurses_into_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.md").write_text("x")
    (tmp_path / "top.md").write_text("y")
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in walk(tmp_path))
    assert rel == ["sub/nested.md", "top.md"]


def test_walk_skips_dot_obsidian_folder(tmp_path: Path) -> None:
    obsidian = tmp_path / ".obsidian"
    obsidian.mkdir()
    (obsidian / "appearance.md").write_text("x")
    (tmp_path / "real.md").write_text("y")
    assert [p.name for p in walk(tmp_path)] == ["real.md"]


def test_walk_skips_dot_git_folder(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "leaked.md").write_text("x")
    (tmp_path / "real.md").write_text("y")
    assert [p.name for p in walk(tmp_path)] == ["real.md"]


def test_walk_respects_gitignore_file_patterns(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.md\n")
    (tmp_path / "ignored.md").write_text("x")
    (tmp_path / "kept.md").write_text("y")
    assert [p.name for p in walk(tmp_path)] == ["kept.md"]


def test_walk_respects_ignore_file_patterns(tmp_path: Path) -> None:
    """Spec §8: `.ignore` excludes the tool's generated reports on re-runs."""
    (tmp_path / ".ignore").write_text("report-*.md\n")
    (tmp_path / "report-2026-05-24.md").write_text("x")
    (tmp_path / "note.md").write_text("y")
    assert [p.name for p in walk(tmp_path)] == ["note.md"]


def test_walk_respects_gitignore_directory_patterns(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("private/\n")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "secret.md").write_text("x")
    (tmp_path / "public.md").write_text("y")
    assert [p.name for p in walk(tmp_path)] == ["public.md"]


def test_walk_honors_gitignore_and_ignore_together(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("scratch.md\n")
    (tmp_path / ".ignore").write_text("report-*.md\n")
    (tmp_path / "scratch.md").write_text("x")
    (tmp_path / "report-1.md").write_text("y")
    (tmp_path / "keep.md").write_text("z")
    assert [p.name for p in walk(tmp_path)] == ["keep.md"]


def test_walk_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "via_link.md").write_text("x")

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "real.md").write_text("y")
    (vault / "link").symlink_to(outside)

    names = [p.name for p in walk(vault)]
    assert "real.md" in names
    assert "via_link.md" not in names


def test_walk_yields_deterministic_order(tmp_path: Path) -> None:
    """Sorted iteration so reports and audit logs are reproducible."""
    for name in ["c.md", "a.md", "b.md"]:
        (tmp_path / name).write_text("")
    assert [p.name for p in walk(tmp_path)] == ["a.md", "b.md", "c.md"]
