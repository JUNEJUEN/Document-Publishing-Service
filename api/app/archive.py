"""Our own archive copies, stored durably and checked by sha256."""
import hashlib
import re
from pathlib import Path

from .files import write_durably


class ArchiveError(Exception):
    pass


def safe_filename(name: str) -> str:
    """The last path part, limited to letters, digits, dot, underscore and hyphen."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".") or "document"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Archive:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self._root = root.resolve()

    def store(self, document_id: str, filename: str, content: bytes) -> tuple[str, str]:
        """Saves the content; returns its path relative to the root, and its sha256."""
        digest = _sha256(content)
        relative = f"{document_id}/{digest}/{safe_filename(filename)}"
        if not self.matches(relative, digest):
            path = self._path(relative)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                write_durably(path, content, ".partial")
            except OSError as exc:  # the type only: no server paths in user-facing errors
                raise ArchiveError(f"could not write the archive copy ({type(exc).__name__})") from exc
            if not self.matches(relative, digest):
                raise ArchiveError("archive copy failed its hash check")
        return relative, digest

    def read(self, relative: str, digest: str) -> bytes | None:
        """The archive copy, or None when it can't be read or its sha256 differs."""
        try:
            content = self._path(relative).read_bytes()
        except OSError:
            return None
        return content if _sha256(content) == digest else None

    def matches(self, relative: str, digest: str) -> bool:
        """True when the archive copy can be read and has this sha256."""
        return self.read(relative, digest) is not None

    def _path(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if not path.is_relative_to(self._root):
            raise ArchiveError("archive path leaves the archive root")
        return path
