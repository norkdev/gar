"""Document reader for the private idea source.

v1: Markdown only. The file content is returned verbatim — no front-matter
stripping, no Markdown-to-text conversion. Consumers (search, agent) decide
what to do with the structure.

PDF support is Future Work; it will extend the suffix dispatch here together
with `ideas/walker.py`'s file-type filter.
"""

from dataclasses import dataclass
from pathlib import Path


class UnsupportedFileType(ValueError):
    """Raised when a file's type cannot be read in the current version."""


@dataclass(frozen=True)
class IdeaDocument:
    """One document read from the private idea source."""

    path: Path
    content: str


def read(path: Path) -> IdeaDocument:
    """Read a single idea document.

    Raises FileNotFoundError if the path does not exist.
    Raises UnsupportedFileType for any file type not supported in v1.
    """
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _read_markdown(path)
    raise UnsupportedFileType(
        f"Unsupported file type '{path.suffix}' for {path}"
    )


def _read_markdown(path: Path) -> IdeaDocument:
    return IdeaDocument(path=path, content=path.read_text(encoding="utf-8"))
