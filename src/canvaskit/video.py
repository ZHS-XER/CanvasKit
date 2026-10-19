"""SJTU lecture-recording platform (v.sjtu.edu.cn), reached via Canvas LTI.

Auth chain, token only:
  Canvas API sessionless_launch -> one-time Canvas URL -> LTI 1.3 OIDC forms
  -> redirect carrying `jwt_token` -> resource-manage API with `jwt-token` header.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from .canvas import USER_AGENT, CanvasClient, CanvasError, safe_name

VIDEO_TOOL_ID = 8329  # "课堂视频新版"
RESOURCE_BASE = "https://v.sjtu.edu.cn/jy-application-resourcemanage"
RESOURCE_UI = "https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/"
# Shared by every VideoClient, so syncing several courses at once still keeps the platform's load modest.
MAX_IN_FLIGHT = 6
_slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)

WEEKDAYS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


class VideoError(Exception):
    pass


@dataclass
class Lecture:
    id: int
    subject: str
    teacher: str
    classroom: str
    begin: str  # "YYYY-MM-DD HH:MM:SS"
    end: str
    week: int
    weekday: int
    period: int  # school period number (letiNumber)
    daily_index: int  # 1-based order among this course's lectures on the same day

    @property
    def date(self) -> str:
        return self.begin[:10]

    @property
    def title(self) -> str:
        """Matches the naming used by SJTU Canvas Helper: '高等数学 第3周 周一 第1节'.

        Used as a file name, so path separators in the subject (e.g. 'C/C++') are replaced."""
        if self.week > 0 and self.weekday in WEEKDAYS:
            return safe_name(f"{self.subject} 第{self.week}周 {WEEKDAYS[self.weekday]} 第{self.daily_index}节")
        return safe_name(f"{self.subject} {self.begin.replace(':', '-')}")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "date": self.date, "title": self.title}


@dataclass
class SubtitleLine:
    begin_ms: int
    end_ms: int
    text: str


@dataclass
class Transcript:
    sentences: list[SubtitleLine] = field(default_factory=list)  # fine-grained, for SRT
    paragraphs: list[SubtitleLine] = field(default_factory=list)  # merged, for reading


@dataclass
class Slide:
    seek_seconds: int
    image_url: str
    ocr_text: str
    keywords: list[str]
    # What was on screen: "slideshow" (full-screen slide), "editor" (PowerPoint
    # edit view, see deck / slide_no), or "other" (desktop, browser, Word, ...).
    kind: str = "slideshow"
    deck: str | None = None  # deck title read from the PowerPoint title bar
    slide_no: int | None = None  # "幻灯片第N张，共M张" in the PowerPoint status bar
    slide_total: int | None = None
    at: str | None = None  # wall-clock time, lecture begin + seek_seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.method = "GET"
        self.fields: dict[str, str] = {}
        self._in_form = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form" and not self._done and not self._in_form:
            self._in_form = True
            self.action = a.get("action", "")
            self.method = (a.get("method") or "GET").upper()
        elif tag == "input" and self._in_form and a.get("name"):
            self.fields[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False
            self._done = True


def parse_form(html: str) -> tuple[str, str, dict[str, str]] | None:
    p = _FormParser()
    p.feed(html)
    if p.action is None:
        return None
    return p.action, p.method, p.fields


def jwt_from_url(url: str) -> str | None:
    m = re.search(r"[?&#]jwt_token=([^&#]+)", url)
    return unquote(m.group(1)) if m else None


def _api_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise VideoError(f"Unexpected response: {str(payload)[:200]}")
    if payload.get("ok") is False or payload.get("dataOk") is False:
        raise VideoError(f"Video platform error: {payload.get('message') or payload}")
    return payload.get("data")


def lectures_from_records(records: list[dict[str, Any]]) -> list[Lecture]:
    """Build lectures sorted oldest first, numbering same-day lectures 1, 2, ..."""
    rows = sorted(records, key=lambda r: r.get("courBeginTime") or "")
    per_day: dict[str, int] = defaultdict(int)
    lectures = []
    for r in rows:
        begin = r.get("courBeginTime") or ""
        per_day[begin[:10]] += 1
        teachers = r.get("teclTeacNames") or r.get("teacNames") or []
        lectures.append(Lecture(
            id=int(r["id"]),
            subject=r.get("subjName") or r.get("teclName") or "",
            teacher=r.get("tecName") or ",".join(teachers),
            classroom=r.get("clroName") or "",
            begin=begin,
            end=r.get("courEndTime") or "",
            week=int(r.get("weekNumber") or r.get("weekNo") or 0),
            weekday=int(r.get("week") or 0),
            period=int(r.get("letiNumber") or 0),
            daily_index=per_day[begin[:10]],
        ))
    return lectures


def _lines(items: list[dict[str, Any]]) -> list[SubtitleLine]:
    out = []
    for it in items:
        text = (it.get("res") or "").strip()
        if text:
            out.append(SubtitleLine(int(it.get("bg") or 0), int(it.get("ed") or 0), text))
    out.sort(key=lambda s: s.begin_ms)
    return out


def transcript_from_data(data: dict[str, Any]) -> Transcript:
    return Transcript(
        sentences=_lines(data.get("beforeAssemblyList") or []),
        paragraphs=_lines(data.get("afterAssemblyList") or []),
    )


# Floating toolbar of the classroom capture client, present in most snapshots.
_UI_NOISE = re.compile(r"保存\s*切换\s*更多")
_UI_KEYWORDS = {"保存", "切换", "更多"}
_SLIDE_NO = re.compile(r"幻灯片\s*[^\d\s]?\s*(\d{1,3})\s*张\s*[，,]\s*共\s*(\d{1,3})\s*张")
_PPT_TITLE = re.compile(r"^(.{1,100}?)\s*-\s*PowerPoint")
_DECK_START = re.compile(r"\d{1,2}-|第\s*\d")
_OTHER_SCREEN = ("此电脑", "回收站", "放映结束", "GoogleChrome", "Google Chrome", "MicrosoftEdge",
                 "Microsoft Edge", " - Word", "- Word", "在此键入进行搜索")  # last: Windows taskbar visible


def _deck_title(ocr: str) -> str | None:
    m = _PPT_TITLE.search(ocr[:160])
    if not m:
        return None
    title = re.sub(r"\.pptx?$", "", m.group(1).strip(), flags=re.I).strip()
    # Window-control glyphs are OCR'd in front of the title: "日5回7-多元函数..."
    start = _DECK_START.search(title)
    if start:
        title = title[start.start():]
    else:
        title = re.sub(r"^[^\u4e00-\u9fffA-Za-z0-9]+", "", title)
    return title or None


def classify_slide(ocr: str) -> tuple[str, str | None, int | None, int | None]:
    """Return (kind, deck, slide_no, slide_total) from a snapshot's OCR text."""
    no = _SLIDE_NO.search(ocr)
    deck = _deck_title(ocr)
    if no or deck:
        return ("editor", deck, int(no.group(1)) if no else None, int(no.group(2)) if no else None)
    if any(marker in ocr for marker in _OTHER_SCREEN):
        return ("other", None, None, None)
    return ("slideshow", None, None, None)


def _wall_clock(begin: str, seconds: int) -> str | None:
    try:
        start = datetime.strptime(begin, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (start + timedelta(seconds=seconds)).strftime("%H:%M:%S")


def slides_from_data(data: dict[str, Any], lecture_begin: str = "") -> list[Slide]:
    slides = []
    for d in data.get("docList") or []:
        if d.get("showFlag") is False:
            continue
        raw = d.get("ocrText") or ""
        kind, deck, slide_no, slide_total = classify_slide(raw)
        seek = int(d.get("imageSeekTime") or 0)
        slides.append(Slide(
            seek_seconds=seek,
            image_url=d.get("imageUrl") or "",
            ocr_text=_UI_NOISE.sub("", raw).strip(),
            keywords=[k for k in d.get("textKeywordList") or [] if k not in _UI_KEYWORDS],
            kind=kind,
            deck=deck,
            slide_no=slide_no,
            slide_total=slide_total,
            at=_wall_clock(lecture_begin, seek),
        ))
    slides.sort(key=lambda s: s.seek_seconds)
    return slides


class VideoClient:
    """One instance is bound to one Canvas course (the JWT carries the course context)."""

    def __init__(self, canvas: CanvasClient, course_id: int, timeout: float = 30.0):
        self.canvas = canvas
        self.course_id = course_id
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
        self._jwt: str | None = None
        self._jwt_lock = threading.Lock()
        self._teaching_class_id: int | None = None

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "VideoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- auth --------------------------------------------------------------

    def _launch(self) -> str:
        try:
            launch = self.canvas.get(
                f"/api/v1/courses/{self.course_id}/external_tools/sessionless_launch", id=VIDEO_TOOL_ID
            )
        except CanvasError as e:
            raise VideoError(f"This course has no lecture-recording tool or it is not accessible ({e})") from e
        url, method, data = launch["url"], "GET", None
        for _ in range(15):
            resp = self.http.request(method, url, data=data)
            if resp.is_redirect:
                url = urljoin(str(resp.url), resp.headers["location"])
                if token := jwt_from_url(url):
                    return token
                method, data = "GET", None
                continue
            if resp.status_code >= 400:
                raise VideoError(f"LTI launch failed at {resp.url.host}{resp.url.path}: HTTP {resp.status_code}")
            form = parse_form(resp.text)
            if form is None:
                raise VideoError(f"LTI launch stalled at {resp.url.host}{resp.url.path} (no form, no redirect)")
            action, method, data = form
            url = urljoin(str(resp.url), action)
        raise VideoError("LTI launch did not produce a jwt_token")

    @property
    def jwt(self) -> str:
        with self._jwt_lock:  # requests may run on several threads; log in only once
            if self._jwt is None:
                self._jwt = self._launch()
            return self._jwt

    def _get(self, path: str, **params: Any) -> Any:
        headers = {"jwt-token": self.jwt, "Referer": RESOURCE_UI}
        with _slots:
            resp = self.http.get(f"{RESOURCE_BASE}{path}", params=params or None, headers=headers)
        if resp.status_code >= 400:
            raise VideoError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return _api_data(resp.json())

    # -- data --------------------------------------------------------------

    @property
    def teaching_class_id(self) -> int:
        if self._teaching_class_id is None:
            data = self._get("/lms/launch-context")
            try:
                self._teaching_class_id = int(data["canvasRecord"]["teachingClassId"])
            except (TypeError, KeyError, ValueError) as e:
                raise VideoError("Video platform did not return a teaching class for this course") from e
        return self._teaching_class_id

    def lectures(self) -> list[Lecture]:
        data = self._get(
            "/v1/subject_vod_list_new",
            **{
                "page.pageIndex": 1,
                "page.pageSize": 1000,
                "teclIds": self.teaching_class_id,
                "page.orders[0].asc": "false",
                "page.orders[0].field": "courBeginTime",
                "schoolOpenStatusFlag": "false",
            },
        )
        return lectures_from_records((data or {}).get("records") or [])

    def transcript(self, lecture_id: int) -> Transcript:
        data = self._get(f"/v1/course/ai/translate/{lecture_id}", useOriginal="true")
        return transcript_from_data(data or {})

    def slides(self, lecture: Lecture) -> list[Slide]:
        data = self._get("/v1/course/ai/ppt", courseId=lecture.id)
        return slides_from_data(data or {}, lecture.begin)
