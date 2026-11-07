"""Atomic JSON writes shared by the stubs."""
import json
import os
from pathlib import Path


def save_json(path: Path, data) -> None:
    # Temp file, fsync, rename: the saved file is never half-written.
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
