import hashlib

import pytest

from app.archive import Archive, ArchiveError, safe_filename

CONTENT = b"Contract doc-1\n"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


@pytest.fixture
def archive(tmp_path) -> Archive:
    return Archive(tmp_path / "archive")


def test_stores_under_a_stable_path(archive, tmp_path):
    relative, digest = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert (relative, digest) == (f"doc-1/{DIGEST}/doc-1.txt", DIGEST)
    assert (tmp_path / "archive" / relative).read_bytes() == CONTENT


def test_storing_twice_keeps_the_first_copy(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    written = (tmp_path / "archive" / relative).stat().st_mtime_ns
    assert archive.store("doc-1", "doc-1.txt", CONTENT) == (relative, DIGEST)
    assert (tmp_path / "archive" / relative).stat().st_mtime_ns == written


def test_leaves_no_partial_file(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert [p.name for p in (tmp_path / "archive" / relative).parent.iterdir()] == ["doc-1.txt"]


def test_replaces_a_damaged_copy(archive, tmp_path):
    relative, _ = archive.store("doc-1", "doc-1.txt", CONTENT)
    (tmp_path / "archive" / relative).write_bytes(b"damaged")
    archive.store("doc-1", "doc-1.txt", CONTENT)
    assert (tmp_path / "archive" / relative).read_bytes() == CONTENT


def test_detects_a_damaged_or_missing_copy(archive, tmp_path):
    relative, digest = archive.store("doc-1", "doc-1.txt", CONTENT)
    assert archive.matches(relative, digest)
    path = tmp_path / "archive" / relative
    path.write_bytes(b"damaged")
    assert not archive.matches(relative, digest)
    path.unlink()
    assert not archive.matches(relative, digest)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("doc-1.txt", "doc-1.txt"),
        ("my report (v2).pdf", "my_report__v2_.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\temp\\a.txt", "a.txt"),
        (".hidden", "hidden"),
        ("..", "document"),
        ("", "document"),
    ],
)
def test_cleans_filenames(name, expected):
    assert safe_filename(name) == expected


def test_refuses_paths_outside_the_root(archive):
    with pytest.raises(ArchiveError, match="leaves the archive root"):
        archive.store("..", "x.txt", CONTENT)
    with pytest.raises(ArchiveError, match="leaves the archive root"):
        archive.matches("../outside.txt", DIGEST)
