"""Crash-safe file writes."""
import os
from pathlib import Path


def write_durably(path: Path, data: bytes, temp_suffix: str) -> None:
    """Writes a temp file, fsyncs it, renames it over `path`, then fsyncs the folder."""
    temp = path.with_name(f"{path.name}{temp_suffix}")
    with temp.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)
    if os.name != "nt":  # Windows cannot fsync a folder
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
