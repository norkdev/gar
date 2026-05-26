"""ideas/reader unit tests."""

from pathlib import Path

import pytest
from gar_backend.ideas.reader import IdeaDocument, UnsupportedFileType, read


def test_read_returns_idea_document_with_content(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("hello\nworld")
    doc = read(f)
    assert isinstance(doc, IdeaDocument)
    assert doc.content == "hello\nworld"


def test_read_preserves_path_on_document(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("x")
    assert read(f).path == f


def test_read_empty_file_returns_empty_content(tmp_path: Path) -> None:
    f = tmp_path / "empty.md"
    f.write_text("")
    assert read(f).content == ""


def test_read_preserves_yaml_front_matter_verbatim(tmp_path: Path) -> None:
    """v1: front matter is not stripped. Consumers decide how to use it."""
    content = "---\ntitle: My Note\ntags: [idea]\n---\n\nBody text."
    f = tmp_path / "note.md"
    f.write_text(content)
    assert read(f).content == content


def test_read_preserves_multibyte_utf8_content(tmp_path: Path) -> None:
    content = "アイデアの草稿\n\nテストです。"
    f = tmp_path / "japanese.md"
    f.write_text(content, encoding="utf-8")
    assert read(f).content == content


def test_read_unsupported_extension_raises(tmp_path: Path) -> None:
    f = tmp_path / "note.txt"
    f.write_text("x")
    with pytest.raises(UnsupportedFileType):
        read(f)


def test_read_pdf_extension_is_unsupported_in_v1(tmp_path: Path) -> None:
    """PDF is Future Work — this test documents v1 behavior so the seam is explicit."""
    f = tmp_path / "paper.pdf"
    f.write_bytes(b"%PDF-1.4 stub")
    with pytest.raises(UnsupportedFileType):
        read(f)


def test_read_extension_match_is_case_insensitive(tmp_path: Path) -> None:
    f = tmp_path / "note.MD"
    f.write_text("hello")
    assert read(f).content == "hello"


def test_read_nonexistent_path_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read(tmp_path / "nope.md")
