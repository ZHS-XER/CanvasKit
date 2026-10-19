"""CanvasClient against a fake Canvas (httpx.MockTransport): the parallel file index keeps the
source priority and order, and throttled requests are retried."""

import threading
import time

import httpx
import pytest

from canvaskit import canvas
from canvaskit.canvas import CanvasClient, CanvasError, parallel
from canvaskit.config import Config

BASE = "https://canvas.test"


def client(handler) -> CanvasClient:
    c = CanvasClient(Config("token", BASE))
    c.http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler))
    return c


def meta(id, name, folder_id=1):
    return {"id": id, "display_name": name, "folder_id": folder_id, "size": 10, "url": f"{BASE}/dl/{id}",
            "updated_at": "2026-10-01T00:00:00Z", "modified_at": "2026-10-01T00:00:00Z"}


C = "/api/v1/courses/7"
ROUTES = {
    f"{C}/folders": [{"id": 1, "full_name": "course files"}, {"id": 2, "full_name": "course files/slides"}],
    f"{C}/files": [meta(101, "ch1.pdf", 2), meta(102, "old.pdf")],
    f"{C}/files?page=2": [meta(103, "syllabus.pdf")],
    f"{C}/modules": [{"id": 1, "name": "第一章", "items": [{"type": "File", "content_id": 101}, {"type": "Page"}]},
                     {"id": 2, "name": "第二章"}],  # no inline items: fetched separately
    f"{C}/modules/2/items": [{"type": "File", "content_id": 201}],
    C: {"syllabus_body": '<a href="/courses/7/files/103/download">'},
    f"{C}/front_page": {"url": "home", "body": '<a href="/courses/7/files/202">'},
    f"{C}/pages": [{"url": "home", "title": "Home"}, {"url": "notes", "title": "笔记"}],
    f"{C}/pages/notes": {"body": '<a href="/files/203">'},
    f"{C}/assignments": [],
    "/api/v1/announcements": [{"title": "通知", "message": '<a href="/files/204">', "attachments": [{"id": 205}]}],
    f"{C}/files/201": meta(201, "hidden.pdf", 2),
    f"{C}/files/203": meta(203, "notes.pdf"),
    f"{C}/files/204": meta(204, "notice.pdf"),
    f"{C}/files/205": meta(205, "attachment.pdf"),
}


def fake_canvas(requested: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path + ("?page=2" if request.url.params.get("page") == "2" else "")
        requested.append(path)
        if path not in ROUTES:  # e.g. discussions disabled, file 202 deleted
            return httpx.Response(404, json={"errors": [{"message": "not found"}]})
        headers = {"link": f'<{BASE}{C}/files?page=2>; rel="next"'} if path == f"{C}/files" else {}
        return httpx.Response(200, json=ROUTES[path], headers=headers)
    return handler


def test_course_files_sources_and_order():
    requested: list[str] = []
    files = client(fake_canvas(requested)).course_files(7)
    assert [(f.id, f.source, f.folder, f.hidden) for f in files] == [
        (101, "单元：第一章", "slides", False),
        (201, "单元：第二章", "slides", True),
        (102, "文件库", "", False),
        (103, "文件库", "", False),  # listed files keep 文件库 even when a page also links them
        (203, "链接：页面 笔记", "", True),
        (205, "链接：公告 通知", "", True),  # attachments before links within one item
        (204, "链接：公告 通知", "", True),
    ]
    assert f"{C}/pages/home" not in requested  # the front page is not fetched twice
    assert f"{C}/files/101" not in requested  # listed files need no extra lookup
    assert requested.count(f"{C}/files/202") == 1


def test_course_files_server_error_is_not_swallowed():
    def handler(request):
        if request.url.path == f"{C}/assignments":
            return httpx.Response(500, text="boom")
        return fake_canvas([])(request)
    with pytest.raises(CanvasError, match="500"):
        client(handler).course_files(7)


def test_rate_limited_requests_are_retried(monkeypatch):
    monkeypatch.setattr(canvas.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) <= 2:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, json={"id": 1})
    assert client(handler).get("/api/v1/users/self") == {"id": 1}
    assert len(calls) == 3


def test_plain_forbidden_is_not_retried(monkeypatch):
    monkeypatch.setattr(canvas.time, "sleep", lambda s: None)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(403, json={"status": "unauthorized"})
    with pytest.raises(CanvasError, match="403"):
        client(handler).get("/api/v1/courses/7/front_page")
    assert len(calls) == 1


def test_requests_in_flight_are_bounded(monkeypatch):
    monkeypatch.setattr(canvas, "MAX_IN_FLIGHT", 2)
    lock, active, peak = threading.Lock(), [0], [0]

    def handler(request):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return httpx.Response(200, json={})
    c = client(handler)
    parallel([lambda: c.get("/x") for _ in range(8)], workers=8)
    assert peak[0] == 2


def test_parallel_keeps_order_and_raises():
    assert parallel([lambda i=i: (time.sleep(0.01 * (5 - i)), i)[1] for i in range(5)]) == [0, 1, 2, 3, 4]
    assert parallel([]) == []

    def boom():
        raise ValueError("boom")
    with pytest.raises(ValueError, match="boom"):
        parallel([lambda: 1, boom, lambda: 3])
