from pathlib import Path

import pytest

from canvaskit.canvas import CourseFile
from canvaskit.config import ConfigError, find_course_config, load_courses
from canvaskit.library import Manifest, destination, local_index, reconcile_local


def cf(id=1, name="a.pdf", size=10, modified="2026-09-01T00:00:00Z", locked=False, source="文件库"):
    return CourseFile(id=id, name=name, folder="", size=size, updated_at=modified, modified_at=modified,
                      url="" if locked else "https://x/files/1/download", locked=locked, source=source)


# -- config ------------------------------------------------------------------


def test_load_courses_defaults_and_lookup(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[courses."高数"]\ncanvas_id = 12345\ndir = "~/课/高数"\nsubtitles = "课程字幕"\n'
        '[courses."物理"]\ncanvas_id = 23456\ndir = "/tmp/物理"\n',
        encoding="utf-8",
    )
    courses = load_courses(p)
    v, e = courses["高数"], courses["物理"]
    assert v.dir == Path.home() / "课" / "高数"
    assert v.subtitles_dir == v.dir / "课程字幕" and v.slides_dir == v.dir / "slides"
    assert e.subtitles == "subtitles" and e.formats == ["srt", "txt"]
    assert find_course_config("高数", courses) is v
    assert find_course_config("23456", courses) is e
    assert find_course_config("高数A", courses) is v  # key contained in the query
    assert find_course_config("化学", courses) is None
    assert load_courses(tmp_path / "missing.toml") == {}


def test_find_course_config_prefers_closer_matches(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[courses."物理"]\ncanvas_id = 1\ndir = "/tmp/a"\n'
        '[courses."大学物理实验"]\ncanvas_id = 2\ndir = "/tmp/b"\n'
        '[courses."高数"]\ncanvas_id = 3\ndir = "/tmp/c"\n'
        '[courses."高数习题"]\ncanvas_id = 4\ndir = "/tmp/d"\n',
        encoding="utf-8",
    )
    courses = load_courses(p)
    assert find_course_config("物理实验", courses) is courses["大学物理实验"]  # part of a key beats key in query
    assert find_course_config("高", courses) is None  # ambiguous
    assert find_course_config("2", courses) is courses["大学物理实验"]  # canvas id


def test_load_courses_rejects_incomplete(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[courses.x]\ncanvas_id = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_courses(p)


# -- manifest ----------------------------------------------------------------


def test_manifest_lifecycle(tmp_path):
    m = Manifest(42, tmp_path)
    f = cf()
    assert m.state(f).status == "new"
    m.mark_seen(f)
    assert m.state(f).status == "pending"
    m.mark_downloaded(f, tmp_path / "slides" / "a.pdf")
    assert m.state(f).status == "downloaded"
    m.mark_seen(f)  # never downgrades a file we have
    assert m.state(f).status == "downloaded"
    # Teacher replaced the content: size / modified_at change.
    assert m.state(cf(size=11)).status == "updated"
    assert m.state(cf(modified="2026-10-01T00:00:00Z")).status == "updated"
    m.save()
    reloaded = Manifest(42, tmp_path)
    assert reloaded.state(f).status == "downloaded"
    assert reloaded.files["1"]["path"].endswith("a.pdf")


def test_locked_file_is_announced_again_when_opened(tmp_path):
    m = Manifest(1, tmp_path)
    locked = cf(locked=True)
    assert m.state(locked).status == "locked"
    m.mark_seen(locked)
    assert m.state(locked).status == "locked"
    assert m.state(cf()).status == "new"  # now open: announce again
    m.mark_seen(cf())
    assert m.state(cf()).status == "pending"


def test_reconcile_local_matches_name_and_size(tmp_path):
    root = tmp_path / "course"
    (root / "2025").mkdir(parents=True)
    (root / "2025" / "a.pdf").write_bytes(b"x" * 10)
    (root / "b.pdf").write_bytes(b"x" * 3)  # same name as a Canvas file but different size
    (root / ".hidden").mkdir()
    (root / ".hidden" / "c.pdf").write_bytes(b"x" * 5)
    m = Manifest(1, tmp_path / "state")
    files = [cf(1, "a.pdf", 10), cf(2, "b.pdf", 4), cf(3, "c.pdf", 5)]
    matched = reconcile_local(m, files, root)
    assert [s.file.id for s in matched] == [1]
    assert m.state(files[0]).status == "local"
    assert m.state(files[1]).status == "new"
    assert m.state(files[2]).status == "new"  # dot directories are ignored
    assert reconcile_local(m, files[:1], root) == []  # already known


def test_local_index_skips_partial_downloads(tmp_path):
    (tmp_path / "x.pdf.part").write_bytes(b"1")
    (tmp_path / "x.pdf").write_bytes(b"12")
    assert list(local_index(tmp_path)) == [("x.pdf", 2)]


def test_destination_flat_with_collisions(tmp_path):
    slides = tmp_path / "slides"
    slides.mkdir()
    m = Manifest(1, tmp_path / "state")
    taken: set[str] = set()
    assert destination(cf(1, "a.pdf"), slides, m, taken) == slides / "a.pdf"
    # Same display name from another folder in the same run.
    assert destination(cf(2, "a.pdf"), slides, m, taken) == slides / "a (2).pdf"
    # An unrelated file of the user's with the same name is never overwritten.
    (slides / "b.pdf").write_bytes(b"mine")
    assert destination(cf(3, "b.pdf", size=99), slides, m, set()) == slides / "b (3).pdf"
    # Re-downloading an updated file reuses our own earlier path.
    m.mark_downloaded(cf(4, "c.pdf"), slides / "c (4).pdf")
    assert destination(cf(4, "c.pdf"), slides, m, set()) == slides / "c (4).pdf"
    # A file the user keeps elsewhere gets a fresh copy in slides.
    m.mark_local(cf(5, "d.pdf"), tmp_path / "2025" / "d.pdf")
    assert destination(cf(5, "d.pdf"), slides, m, set()) == slides / "d.pdf"
