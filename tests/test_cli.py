"""Command-level tests with fake Canvas / video clients: stdout stays pure JSON, one failing item
does not stop a batch, and interrupted or partial work is picked up again on the next run."""

import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from canvaskit import cli, library
from canvaskit.canvas import CanvasError, Course
from canvaskit.config import CourseConfig
from canvaskit.video import Lecture, Slide, SubtitleLine, Transcript

runner = CliRunner()
COURSE = Course(1, "C/C++程序设计", "CS101", None)


def lecture(id, day, weekday):
    return Lecture(id, COURSE.name, "", "", f"2026-10-{day} 08:00:00", f"2026-10-{day} 09:40:00", 5, weekday, 1, 1)


LECTURES = [lecture(11, "12", 1), lecture(12, "14", 3)]
TRANSCRIPT = Transcript([SubtitleLine(0, 1000, "第一句")], [SubtitleLine(0, 1000, "第一句")])


class FakeCanvas:
    def __init__(self, files_error=False):
        self.files_error = files_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def resolve_course(self, query):
        return COURSE

    def course_files(self, course_id):
        if self.files_error:
            raise CanvasError("GET /api/v1/courses/1/files -> 500: boom")
        return []


class FakeHttp:
    def __init__(self, fail_urls=()):
        self.fail_urls = set(fail_urls)

    def get(self, url):
        request = httpx.Request("GET", url)
        if url in self.fail_urls:
            return httpx.Response(500, request=request)
        return httpx.Response(200, content=b"jpeg", request=request)


class FakeVideo:
    http = FakeHttp()

    def __init__(self, canvas, course_id):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def close(self):
        pass

    def lectures(self):
        return LECTURES

    def transcript(self, lecture_id):
        return TRANSCRIPT

    def slides(self, lec):
        return [Slide(i * 60, f"https://img/{i}.jpg", f"第{i}页", []) for i in range(1, 4)]


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_client", lambda: FakeCanvas())
    monkeypatch.setattr(cli, "VideoClient", FakeVideo)
    monkeypatch.setattr(cli, "_courses_config", lambda: {})
    monkeypatch.setattr(library, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(FakeVideo, "http", FakeHttp())
    return tmp_path


def run_json(*args):
    result = runner.invoke(cli.app, [*args, "--json"])
    return result, json.loads(result.stdout)


def test_utf8_streams_fix_windows_code_pages(monkeypatch):
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.StringIO())  # no reconfigure(): must be skipped quietly
    cli._utf8_streams()
    print('{"name": "高等数学 第5周"}')
    stdout.flush()
    assert stdout.buffer.getvalue().decode("utf-8").startswith('{"name": "高等数学')


def test_version():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and result.stdout.startswith("canvaskit ")


def test_subtitles_stdout_is_json_and_titles_are_safe_file_names(fakes):
    out = fakes / "subs"
    result, data = run_json("subtitles", "c", "-o", str(out), "--format", "srt,txt")
    assert result.exit_code == 0
    assert [r["status"] for r in data] == ["downloaded", "downloaded"]
    assert sorted(p.name for p in out.iterdir()) == [
        "C_C++程序设计 第5周 周一 第1节.srt", "C_C++程序设计 第5周 周一 第1节.txt",
        "C_C++程序设计 第5周 周三 第1节.srt", "C_C++程序设计 第5周 周三 第1节.txt",
    ]
    _, again = run_json("subtitles", "c", "-o", str(out))
    assert [r["status"] for r in again] == ["exists", "exists"]


def test_subtitles_write_error_does_not_stop_the_batch(fakes, monkeypatch):
    real_write = cli._write
    calls = []

    def flaky_write(path, data):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(cli, "_write", flaky_write)
    result, data = run_json("subtitles", "c", "-o", str(fakes / "subs"))
    assert result.exit_code == 0
    assert [r["status"] for r in data] == ["error", "downloaded"]
    assert "disk full" in data[0]["error"]
    assert not list((fakes / "subs").glob("*.part"))


def test_snapshots_retry_failed_images(fakes, monkeypatch):
    out = fakes / "shots"
    monkeypatch.setattr(FakeVideo, "http", FakeHttp(fail_urls={"https://img/2.jpg"}))
    _, data = run_json("snapshots", "c", "-o", str(out), "--id", "11", "--images")
    assert data[0]["status"] == "error" and "1 of 3" in data[0]["error"]
    img_dir = Path(data[0]["images_dir"])
    assert len(list(img_dir.iterdir())) == 2

    monkeypatch.setattr(FakeVideo, "http", FakeHttp())
    _, data = run_json("snapshots", "c", "-o", str(out), "--id", "11", "--images")
    assert data[0]["status"] == "downloaded"
    assert len(list(img_dir.iterdir())) == 3

    _, data = run_json("snapshots", "c", "-o", str(out), "--id", "11", "--images")
    assert data[0]["status"] == "exists"


def test_sync_keeps_subtitles_when_file_check_fails(fakes, monkeypatch):
    cfg = CourseConfig(key="cpp", canvas_id=1, dir=fakes / "cpp")
    monkeypatch.setattr(cli, "_courses_config", lambda: {"cpp": cfg})
    monkeypatch.setattr(cli, "_client", lambda: FakeCanvas(files_error=True))
    result, data = run_json("sync")
    assert result.exit_code == 0
    assert [s["status"] for s in data[0]["subtitles"]] == ["downloaded", "downloaded"]
    assert "500" in data[0]["files_error"]


def test_corrupt_manifest_is_a_config_error(fakes):
    state = fakes / "state"
    state.mkdir()
    (state / "1.json").write_text("{not json", encoding="utf-8")
    result = runner.invoke(cli.app, ["files", "c", "--json"])
    assert result.exit_code == 2
    assert result.stdout == ""
    assert "corrupt" in result.stderr


def test_messages_keep_square_brackets(fakes):
    result = runner.invoke(cli.app, ["sync"])
    assert result.exit_code == 2
    assert "[courses.<name>]" in result.stderr


def test_bracketed_names_are_not_rich_markup(fakes, monkeypatch):
    cfg = CourseConfig(key="cpp", canvas_id=1, dir=fakes / "cpp")
    monkeypatch.setattr(cli, "_courses_config", lambda: {"cpp": cfg})
    monkeypatch.setattr(cli, "_client", lambda: FakeCanvas(files_error=True))
    monkeypatch.setattr(FakeCanvas, "course_files", lambda self, cid: (_ for _ in ()).throw(
        CanvasError("[/oops] [files] -> 500")))
    result = runner.invoke(cli.app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "[cpp]" in result.stdout
    assert "[/oops] [files]" in result.stdout
