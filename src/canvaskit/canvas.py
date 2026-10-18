"""Canvas REST API client (token auth only)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

from .config import Config

USER_AGENT = "canvaskit/0.1 (+https://github.com/)"
ROOT_FOLDER = "course files"


class CanvasError(Exception):
    pass


@dataclass
class Course:
    id: int
    name: str
    course_code: str
    term_id: int | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Course":
        return cls(d["id"], d.get("name") or "", d.get("course_code") or "", d.get("enrollment_term_id"))


@dataclass
class CourseFile:
    id: int
    name: str
    folder: str  # Canvas folder relative to the course root, "" for top level / unknown
    size: int
    updated_at: str
    modified_at: str  # content timestamp; changes when the file itself is replaced
    url: str
    locked: bool
    source: str = "文件库"  # where it is reachable: 单元：<module>, 文件库, 链接：<page>, ...
    hidden: bool = False  # not in the Files listing, only reachable through a link

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "folder": self.folder,
            "size": self.size,
            "updated_at": self.updated_at,
            "modified_at": self.modified_at,
            "locked": self.locked,
            "source": self.source,
            "hidden": self.hidden,
        }


FILE_LINK = re.compile(r"/files/(\d+)")


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        m = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part)
        if m and m.group(2) == "next":
            return m.group(1)
    return None


def _relative_folder(full_name: str) -> str:
    """'course files/2025/slides' -> '2025/slides'; 'course files' -> ''."""
    if full_name == ROOT_FOLDER:
        return ""
    if full_name.startswith(ROOT_FOLDER + "/"):
        return full_name[len(ROOT_FOLDER) + 1:]
    return full_name


WINDOWS = os.name == "nt"
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str, windows: bool = WINDOWS) -> str:
    """Make a file name valid on this OS. Windows rules are stricter and only applied there,
    so names on macOS / Linux stay as they were."""
    if not windows:
        return re.sub(r'[/\\:\x00]', "_", name).strip() or "_"
    # Windows also forbids <>"|?* and control characters, drops trailing dots, and reserves device names.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    if name.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        name = "_" + name
    return name or "_"


class CanvasClient:
    def __init__(self, config: Config, timeout: float = 30.0):
        self.config = config
        self.http = httpx.Client(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.token}", "User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "CanvasClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        resp = self.http.get(path, params=params or None)
        self._raise_for_status(resp)
        return resp.json()

    def paginate(self, path: str, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("per_page", 100)
        url: str | None = path
        first = True
        while url:
            resp = self.http.get(url, params=params if first else None)
            self._raise_for_status(resp)
            yield from resp.json()
            url = _next_link(resp.headers.get("link"))
            first = False

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:200]
        raise CanvasError(f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {detail}")

    # -- users / courses ---------------------------------------------------

    def me(self) -> dict[str, Any]:
        return self.get("/api/v1/users/self")

    def courses(self, include_all: bool = False) -> list[Course]:
        params: dict[str, Any] = {}
        if not include_all:
            params["enrollment_state"] = "active"
        return [Course.from_api(c) for c in self.paginate("/api/v1/courses", **params) if "name" in c]

    def resolve_course(self, query: str) -> Course:
        """Accept a numeric id or a case-insensitive substring of the name/code."""
        if query.isdigit():
            return Course.from_api(self.get(f"/api/v1/courses/{query}"))
        q = query.lower()
        for include_all in (False, True):
            matches = [c for c in self.courses(include_all) if q in c.name.lower() or q in c.course_code.lower()]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                exact = [c for c in matches if c.name.lower() == q]
                if len(exact) == 1:
                    return exact[0]
                listing = "; ".join(f"{c.id} {c.name}" for c in matches)
                raise CanvasError(f"'{query}' matches several courses: {listing}. Use the course id.")
        raise CanvasError(f"No course matches '{query}'. Run `canvaskit courses --all` to see them.")

    # -- files ---------------------------------------------------------------

    def _optional(self, fn: Callable[[], Any], default: Any) -> Any:
        """Run a request; treat 401/403/404 (feature hidden or disabled) as empty."""
        try:
            return fn()
        except CanvasError as e:
            if re.search(r"-> 40[134]", str(e)):
                return default
            raise

    def course_files(self, course_id: int) -> list[CourseFile]:
        """Every file of a course a student can reach, each listed once.

        Canvas stores files once (the Files area) and other places only link to
        them. Sources, in priority order for the `source` label:
          1. modules ("单元", usually the course home page), curated by chapter
          2. the Files listing ("文件库"), which also holds old versions and extras
          3. links in the home page, syllabus, pages, assignments, announcements and
             discussions; these can point to hidden files absent from the listing
        """
        listed: dict[int, CourseFile] = {}
        folders = self._optional(
            lambda: {f["id"]: _relative_folder(f["full_name"])
                     for f in self.paginate(f"/api/v1/courses/{course_id}/folders")}, {})
        for f in self._optional(lambda: list(self.paginate(f"/api/v1/courses/{course_id}/files")), []):
            listed[f["id"]] = self._file(f, folders.get(f["folder_id"], ""))

        found: dict[int, CourseFile] = {}

        def add(file_id: int, source: str) -> None:
            if file_id in found:
                return
            if file_id in listed:
                cf = listed[file_id]
                cf.source = source
            else:
                meta = self._optional(lambda: self.get(f"/api/v1/courses/{course_id}/files/{file_id}"), None)
                if meta is None:
                    return
                cf = self._file(meta, folders.get(meta.get("folder_id"), ""), source=source, hidden=True)
            found[file_id] = cf

        for module in self._optional(
                lambda: list(self.paginate(f"/api/v1/courses/{course_id}/modules", **{"include[]": "items"})), []):
            items = module.get("items")
            if items is None:
                items = self._optional(
                    lambda: list(self.paginate(f"/api/v1/courses/{course_id}/modules/{module['id']}/items")), [])
            for item in items:
                if item.get("type") == "File" and item.get("content_id"):
                    add(int(item["content_id"]), f"单元：{module.get('name') or ''}".strip())

        for file_id, cf in listed.items():
            if file_id not in found:
                cf.source = "文件库"
                found[file_id] = cf

        for source, html, attachments in self._linked_content(course_id):
            for att in attachments:
                if att.get("id"):
                    add(int(att["id"]), source)
            for file_id in FILE_LINK.findall(html or ""):
                add(int(file_id), source)
        return list(found.values())

    def _linked_content(self, course_id: int) -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
        base = f"/api/v1/courses/{course_id}"
        info = self._optional(lambda: self.get(base, **{"include[]": "syllabus_body"}), {})
        front = self._optional(lambda: self.get(f"{base}/front_page"), None)
        if front:
            yield "链接：主页", front.get("body") or "", []
        yield "链接：大纲", info.get("syllabus_body") or "", []
        for page in self._optional(lambda: list(self.paginate(f"{base}/pages")), []):
            if front and page.get("url") == front.get("url"):
                continue
            full = self._optional(lambda: self.get(f"{base}/pages/{page['url']}"), None)
            if full:
                yield f"链接：页面 {page.get('title', '')}".strip(), full.get("body") or "", []
        for a in self._optional(lambda: list(self.paginate(f"{base}/assignments")), []):
            yield f"链接：作业 {a.get('name', '')}".strip(), a.get("description") or "", []
        announcements = self._optional(lambda: list(self.paginate(
            "/api/v1/announcements", **{"context_codes[]": f"course_{course_id}"}, start_date="2000-01-01")), [])
        for a in announcements:
            yield f"链接：公告 {a.get('title', '')}".strip(), a.get("message") or "", a.get("attachments") or []
        for d in self._optional(lambda: list(self.paginate(f"{base}/discussion_topics")), []):
            yield f"链接：讨论 {d.get('title', '')}".strip(), d.get("message") or "", d.get("attachments") or []

    @staticmethod
    def _file(f: dict[str, Any], folder: str, source: str = "文件库", hidden: bool = False) -> CourseFile:
        return CourseFile(
            id=f["id"],
            name=safe_name(f.get("display_name") or f.get("filename") or str(f["id"])),
            folder=folder,
            size=f.get("size") or 0,
            updated_at=f.get("updated_at") or "",
            modified_at=f.get("modified_at") or f.get("updated_at") or "",
            url=f.get("url") or "",
            locked=bool(f.get("locked_for_user")) or not f.get("url"),
            source=source,
            hidden=hidden,
        )

    def download(self, url: str, dest: Path, on_progress: Callable[[int], None] | None = None,
                 mtime: str | None = None) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            with self.http.stream("GET", url) as resp:
                self._raise_for_status(resp)
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
                        if on_progress:
                            on_progress(len(chunk))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        if mtime:
            ts = datetime.fromisoformat(mtime.replace("Z", "+00:00")).timestamp()
            os.utime(dest, (ts, ts))
