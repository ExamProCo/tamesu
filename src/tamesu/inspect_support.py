"""Helpers for Inspect scorers that export artifacts to Tamesu.

This module is deliberately stdlib-only so an Inspect task can import it without pulling in
the rest of Tamesu. A scorer stages files while the sandbox is still alive; Tamesu validates
and copies them into canonical item evidence afterwards. Scorers never write item evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

STAGING_ENV = "TAMESU_STAGING_DIR"
MANIFEST_NAME = "artifacts.json"
FILES_DIR = "files"


class Stager:
    def __init__(self, root: Path, sample_id: str) -> None:
        self.directory = root / sample_id
        self.entries: list[dict[str, str]] = []
        (self.directory / FILES_DIR).mkdir(parents=True, exist_ok=True)

    def add(self, role: str, name: str, data: bytes | str, media_type: str) -> None:
        """Stage one artifact. `name` is a relative POSIX path inside the item's artifacts."""
        posix = PurePosixPath(name)
        if posix.is_absolute() or ".." in posix.parts or not posix.parts:
            raise ValueError(f"Unsafe artifact name: {name!r}")
        target = self.directory / FILES_DIR / Path(*posix.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        self.entries.append({"role": role, "name": posix.as_posix(), "media_type": media_type})
        (self.directory / MANIFEST_NAME).write_text(json.dumps(self.entries), encoding="utf-8")


def stager(sample_id: str | int) -> Stager:
    root = Path(os.environ.get(STAGING_ENV) or ".tamesu-staging")
    return Stager(root, str(sample_id))
