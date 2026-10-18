"""Per-course file manifest: what was downloaded, found locally, or already announced.

Stored as ~/.config/canvaskit/state/<course_id>.json, keyed by Canvas file id, so
files the user moves or renames are not downloaded again.

Entry status:
  downloaded  fetched by canvaskit (path = where it was written)
  local       an identical file (same name and size) already existed in the course folder
  seen        announced by `sync`, not downloaded
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canvas import CourseFile
from .config import STATE_DIR, ConfigError

HAVE = ("downloaded", "local")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class FileState:
    """How a Canvas file relates to the manifest right now."""

    file: CourseFile
    status: str  # new | pending | downloaded | local | updated | locked
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**self.file.to_dict(), "status": self.status, "path": self.path}


class Manifest:
    def __init__(self, course_id: int, state_dir: Path | None = None):
        self.course_id = course_id
        self.path = (state_dir or STATE_DIR) / f"{course_id}.json"
        data: dict[str, Any] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
            except ValueError as e:
                raise ConfigError(f"{self.path} is corrupt ({e}). Delete it to rebuild the file list.") from e
        self.files: dict[str, dict[str, Any]] = data.get("files", {})
        self.last_sync: str | None = data.get("last_sync")
        self.last_new: list[int] = data.get("last_new", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"course_id": self.course_id, "last_sync": self.last_sync, "last_new": self.last_new,
             "files": self.files},
            ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # -- queries ---------------------------------------------------------------

    def state(self, f: CourseFile) -> FileState:
        entry = self.files.get(str(f.id))
        if entry and entry["status"] in HAVE:
            changed = entry.get("size") != f.size or entry.get("modified_at") != f.modified_at
            return FileState(f, "updated" if changed and not f.locked else entry["status"], entry.get("path"))
        if f.locked:
            return FileState(f, "locked")
        if entry and entry["status"] == "seen" and not entry.get("locked"):
            return FileState(f, "pending")
        # Unknown, or announced while still locked and now open.
        return FileState(f, "new")

    # -- updates ---------------------------------------------------------------

    def _record(self, f: CourseFile, status: str, path: str | None = None) -> None:
        entry = self.files.setdefault(str(f.id), {"first_seen": now()})
        entry.update(name=f.name, size=f.size, modified_at=f.modified_at, status=status, locked=f.locked)
        if path is not None:
            entry["path"] = path

    def mark_downloaded(self, f: CourseFile, path: Path) -> None:
        self._record(f, "downloaded", str(path))

    def mark_local(self, f: CourseFile, path: Path) -> None:
        self._record(f, "local", str(path))

    def mark_seen(self, f: CourseFile) -> None:
        entry = self.files.get(str(f.id))
        if entry and entry["status"] in HAVE:
            return
        self._record(f, "seen")


def local_index(root: Path, skip: set[Path] = frozenset()) -> dict[tuple[str, int], Path]:
    """(file name, size) -> path for every regular file under root, ignoring dot entries."""
    index: dict[tuple[str, int], Path] = {}
    if not root.is_dir():
        return index
    skip_resolved = {p.resolve() for p in skip}
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and (here / d).resolve() not in skip_resolved]
        for name in filenames:
            if name.startswith(".") or name.endswith(".part"):
                continue
            p = here / name
            try:
                index.setdefault((name, p.stat().st_size), p)
            except OSError:
                continue
    return index


def reconcile_local(manifest: Manifest, files: list[CourseFile], root: Path,
                    skip: set[Path] = frozenset()) -> list[FileState]:
    """Mark files that already exist in the course folder (same name and size) as local."""
    candidates = [f for f in files if manifest.state(f).status in ("new", "pending", "locked")]
    if not candidates:
        return []
    index = local_index(root, skip)
    matched = []
    for f in candidates:
        p = index.get((f.name, f.size))
        if p is not None:
            manifest.mark_local(f, p)
            matched.append(FileState(f, "local", str(p)))
    return matched


def destination(f: CourseFile, slides_dir: Path, manifest: Manifest, taken: set[str]) -> Path:
    """Flat layout inside slides_dir. Re-downloads overwrite our own earlier copy;
    anything else with the same name gets ' (id)' appended instead of being overwritten."""
    entry = manifest.files.get(str(f.id))
    if entry and entry.get("status") == "downloaded" and entry.get("path"):
        prev = Path(entry["path"])
        if prev.parent == slides_dir:
            taken.add(prev.name)
            return prev
    name = f.name
    dest = slides_dir / name
    if name in taken or (dest.exists() and dest.stat().st_size != f.size):
        stem, dot, ext = name.rpartition(".")
        name = f"{stem} ({f.id}).{ext}" if dot else f"{name} ({f.id})"
        dest = slides_dir / name
    taken.add(name)
    return dest
