"""canvaskit command line.

Conventions: results go to stdout (`--json` for machine-readable output),
progress and warnings go to stderr. Existing outputs are skipped unless
`--force`, so re-running a command only fetches what is new.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from importlib.metadata import version
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.table import Table

from .canvas import CanvasClient, CanvasError, Course, CourseFile
from .config import CONFIG_FILE, ConfigError, CourseConfig, find_course_config, load_config, load_courses
from .formats import clock, slides_brief, slides_json, slides_markdown, to_srt, to_text
from . import skill
from .library import FileState, Manifest, destination, now, reconcile_local
from .video import Lecture, VideoClient, VideoError

app = typer.Typer(
    help="Agent-friendly CLI for SJTU Canvas (oc.sjtu.edu.cn): lecture subtitles, slide snapshots, course files.",
    no_args_is_help=True,
    add_completion=False,
)
out = Console()
err = Console(stderr=True)


def _show_version(value: bool) -> None:
    if value:
        sys.stdout.write(f"canvaskit {version('canvaskit')}\n")
        raise typer.Exit()


@app.callback()
def _main(
    _version: bool = typer.Option(False, "--version", callback=_show_version, is_eager=True,
                                  help="Show the version and exit"),
) -> None:
    pass

CourseArg = typer.Argument(..., help="Course id, part of its name (e.g. 高数), or a key from config.toml")
JsonOpt = typer.Option(False, "--json", help="Print machine-readable JSON to stdout")
SUBTITLE_FORMATS = {"srt", "txt"}
REQUEST_ERRORS = (CanvasError, VideoError, httpx.HTTPError)


def _client() -> CanvasClient:
    try:
        return CanvasClient(load_config())
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2)


def _courses_config() -> dict[str, CourseConfig]:
    try:
        return load_courses()
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2)


def _manifest(course_id: int) -> Manifest:
    try:
        return Manifest(course_id)
    except ConfigError as e:
        err.print(f"[red]{e}[/red]")
        raise typer.Exit(2)


def _write(path: Path, data: str | bytes) -> None:
    """Write through a .part file, so an interrupted run never leaves a truncated file that counts as done."""
    tmp = path.with_name(path.name + ".part")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


def _fail(e: Exception) -> None:
    err.print(f"[red]error:[/red] {e}")
    raise typer.Exit(1)


def _emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _resolve(c: CanvasClient, query: str) -> tuple[Course, Optional[CourseConfig]]:
    cfg = find_course_config(query, _courses_config())
    if cfg:
        return c.resolve_course(str(cfg.canvas_id)), cfg
    crs = c.resolve_course(query)
    cfg = next((x for x in _courses_config().values() if x.canvas_id == crs.id), None)
    return crs, cfg


def _out_dir(explicit: Optional[Path], configured: Optional[Path]) -> Path:
    return explicit or configured or Path(".")


def _select(lectures: list[Lecture], latest: Optional[int], since: Optional[str],
            on: Optional[str], ids: Optional[list[int]]) -> list[Lecture]:
    picked = lectures
    if ids:
        wanted = set(ids)
        picked = [lec for lec in picked if lec.id in wanted]
        missing = wanted - {lec.id for lec in picked}
        if missing:
            err.print(f"[yellow]unknown lecture id(s): {', '.join(map(str, sorted(missing)))}[/yellow]")
    if since:
        picked = [lec for lec in picked if lec.date >= since]
    if on:
        picked = [lec for lec in picked if lec.date == on]
    if latest:
        picked = picked[-latest:]
    return picked


def _check_date(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if value == "today":
        return date.today().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise typer.BadParameter("use YYYY-MM-DD or 'today'")


def _parse_formats(fmt: str) -> list[str]:
    formats = [f.strip() for f in fmt.split(",") if f.strip()]
    bad = set(formats) - SUBTITLE_FORMATS
    if bad or not formats:
        raise typer.BadParameter(f"unknown format(s): {', '.join(bad) or fmt}", param_hint="--format")
    return formats


def _count(results: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return ", ".join(f"{k}: {n}" for k, n in counts.items())


OutOpt = typer.Option(None, "--out", "-o", help="Output directory (default: from config.toml, else .)",
                      file_okay=False)
LatestOpt = typer.Option(None, "--latest", "-n", help="Only the N most recent lectures")
SinceOpt = typer.Option(None, "--since", callback=_check_date, help="Lectures on or after YYYY-MM-DD")
OnOpt = typer.Option(None, "--date", callback=_check_date, help="Lectures on YYYY-MM-DD (or 'today')")
IdOpt = typer.Option(None, "--id", help="Lecture id (repeatable), see `canvaskit lectures`")
ForceOpt = typer.Option(False, "--force", "-f", help="Overwrite existing files")


# -- account / courses -----------------------------------------------------


@app.command()
def whoami(as_json: bool = JsonOpt) -> None:
    """Check the token and show the Canvas user."""
    with _client() as c:
        try:
            me = c.me()
        except REQUEST_ERRORS as e:
            _fail(e)
    if as_json:
        _emit_json({"id": me["id"], "name": me.get("name"), "base_url": c.config.base_url})
    else:
        out.print(f"{me.get('name')} (id {me['id']}) @ {c.config.base_url}")


@app.command()
def courses(
    include_all: bool = typer.Option(False, "--all", help="Include finished / inactive courses"),
    as_json: bool = JsonOpt,
) -> None:
    """List your courses (and which ones are configured in config.toml)."""
    configured = {cfg.canvas_id: cfg for cfg in _courses_config().values()}
    with _client() as c:
        try:
            items = c.courses(include_all)
        except REQUEST_ERRORS as e:
            _fail(e)
    if as_json:
        _emit_json([{**vars(x), "config_key": configured[x.id].key if x.id in configured else None,
                     "dir": str(configured[x.id].dir) if x.id in configured else None} for x in items])
        return
    t = Table("id", "name", "code", "config")
    for x in items:
        t.add_row(str(x.id), x.name, x.course_code, configured[x.id].key if x.id in configured else "")
    out.print(t)


# -- lecture recordings ----------------------------------------------------


def _lectures_for(c: CanvasClient, crs: Course) -> tuple[VideoClient, list[Lecture]]:
    v = VideoClient(c, crs.id)
    err.print(f"[dim]{crs.name} (course {crs.id})[/dim]")
    try:
        return v, v.lectures()
    except BaseException:
        v.close()
        raise


@app.command()
def lectures(course: str = CourseArg, as_json: bool = JsonOpt) -> None:
    """List recorded lectures of a course (oldest first)."""
    with _client() as c:
        try:
            crs, _ = _resolve(c, course)
            v, items = _lectures_for(c, crs)
            v.close()
        except REQUEST_ERRORS as e:
            _fail(e)
    if as_json:
        _emit_json([x.to_dict() for x in items])
        return
    t = Table("id", "title", "time", "room")
    for x in items:
        t.add_row(str(x.id), x.title, f"{x.begin[:16]}–{x.end[11:16]}", x.classroom)
    out.print(t)


def _fetch_subtitles(v: VideoClient, items: list[Lecture], out_dir: Path, formats: list[str],
                     force: bool) -> list[dict[str, Any]]:
    """Write missing subtitle files; existing ones are kept unless force."""
    results = []
    for lec in items:
        paths = {f: out_dir / f"{lec.title}.{f}" for f in formats}
        todo = {f: p for f, p in paths.items() if force or not p.exists()}
        entry: dict[str, Any] = {"lecture": lec.to_dict(), "files": [str(p) for p in paths.values()]}
        if not todo:
            entry["status"] = "exists"
            results.append(entry)
            err.print(f"[dim]= {lec.title} (exists)[/dim]")
            continue
        try:
            tr = v.transcript(lec.id)
        except (VideoError, httpx.HTTPError) as e:
            entry.update(status="error", error=str(e), files=[])
            results.append(entry)
            err.print(f"[yellow]! {lec.title}: {e}[/yellow]")
            continue
        if not tr.sentences and not tr.paragraphs:
            entry.update(status="unavailable", files=[])
            results.append(entry)
            err.print(f"[yellow]- {lec.title}: no subtitles yet[/yellow]")
            continue
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for f, p in todo.items():
                _write(p, to_srt(tr) if f == "srt" else to_text(tr, lec))
        except OSError as e:
            entry.update(status="error", error=str(e), files=[])
            results.append(entry)
            err.print(f"[yellow]! {lec.title}: {e}[/yellow]")
            continue
        entry["status"] = "downloaded"
        results.append(entry)
        err.print(f"[green]+ {lec.title}[/green] ({', '.join(todo)})")
    return results


@app.command()
def subtitles(
    course: str = CourseArg,
    out_dir: Optional[Path] = OutOpt,
    fmt: Optional[str] = typer.Option(
        None, "--format", help="Comma-separated: srt, txt (paragraphs with timestamps). "
                               "Default: subtitle_format from config.toml, else srt"),
    latest: Optional[int] = LatestOpt,
    since: Optional[str] = SinceOpt,
    on: Optional[str] = OnOpt,
    ids: Optional[list[int]] = IdOpt,
    force: bool = ForceOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Download lecture subtitles (speech recognition from the recording platform)."""
    with _client() as c:
        try:
            crs, cfg = _resolve(c, course)
            formats = _parse_formats(fmt) if fmt else (cfg.formats if cfg else ["srt"])
            target = _out_dir(out_dir, cfg.subtitles_dir if cfg else None)
            v, items = _lectures_for(c, crs)
        except REQUEST_ERRORS as e:
            _fail(e)
        with v:
            results = _fetch_subtitles(v, _select(items, latest, since, on, ids), target, formats, force)
    if as_json:
        _emit_json(results)
    else:
        out.print(_count(results) or "No lectures matched.")


@app.command()
def snapshots(
    course: str = CourseArg,
    out_dir: Optional[Path] = typer.Option(
        None, "--out", "-o", help="Output directory (default: the subtitles folder from config.toml, else .)",
        file_okay=False),
    images: bool = typer.Option(False, "--images", help="Also download the snapshots (JPEG)"),
    brief: bool = typer.Option(False, "--brief", help="Only a compact summary, one line per snapshot (.slides.brief.md)"),
    latest: Optional[int] = LatestOpt,
    since: Optional[str] = SinceOpt,
    on: Optional[str] = OnOpt,
    ids: Optional[list[int]] = IdOpt,
    force: bool = ForceOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Export the platform's snapshots of the projected screen per lecture: time, OCR, kind, slide number."""
    results = []
    with _client() as c:
        try:
            crs, cfg = _resolve(c, course)
            target = _out_dir(out_dir, cfg.subtitles_dir if cfg else None)
            v, items = _lectures_for(c, crs)
        except REQUEST_ERRORS as e:
            _fail(e)
        with v:
            for lec in _select(items, latest, since, on, ids):
                md = target / f"{lec.title}.slides.md"
                js = target / f"{lec.title}.slides.json"
                bf = target / f"{lec.title}.slides.brief.md"
                img_dir = target / f"{lec.title}-slides"
                targets = [bf] if brief else [md, js]
                entry: dict[str, Any] = {"lecture": lec.to_dict(), "files": [str(p) for p in targets]}
                if images:
                    entry["images_dir"] = str(img_dir)
                missing = force or not all(p.exists() for p in targets)
                # With --images the snapshot list is always fetched, so images that failed last time are retried.
                if not missing and not images:
                    entry["status"] = "exists"
                    results.append(entry)
                    err.print(f"[dim]= {lec.title} (exists)[/dim]")
                    continue
                try:
                    shots = v.slides(lec)
                except (VideoError, httpx.HTTPError) as e:
                    entry.update(status="error", error=str(e), files=[])
                    results.append(entry)
                    err.print(f"[yellow]! {lec.title}: {e}[/yellow]")
                    continue
                if not shots:
                    entry.update(status="unavailable", files=[])
                    results.append(entry)
                    err.print(f"[yellow]- {lec.title}: no snapshots[/yellow]")
                    continue
                names, fetched, failed = None, 0, 0
                try:
                    target.mkdir(parents=True, exist_ok=True)
                    if images:
                        names, fetched, failed = _download_snapshot_images(v.http, shots, img_dir, force)
                    # New images change the links in .slides.md, so rewrite it then too.
                    if missing or fetched:
                        if brief:
                            _write(bf, slides_brief(shots, lec))
                        else:
                            _write(md, slides_markdown(shots, lec, names))
                            _write(js, slides_json(shots, lec))
                except OSError as e:
                    entry.update(status="error", error=str(e), files=[])
                    results.append(entry)
                    err.print(f"[yellow]! {lec.title}: {e}[/yellow]")
                    continue
                entry["count"] = len(shots)
                if failed:
                    entry.update(status="error",
                                 error=f"{failed} of {len(shots)} snapshot images failed; run again to retry")
                    err.print(f"[yellow]! {lec.title}: {failed} snapshot images failed[/yellow]")
                elif missing or fetched:
                    entry["status"] = "downloaded"
                    err.print(f"[green]+ {lec.title}[/green] ({len(shots)} snapshots)")
                else:
                    entry["status"] = "exists"
                    err.print(f"[dim]= {lec.title} (exists)[/dim]")
                results.append(entry)
    if as_json:
        _emit_json(results)
    else:
        out.print(_count(results) or "No lectures matched.")


# Former name, kept so existing scripts keep working.
app.command("slides", hidden=True)(snapshots)


def _download_snapshot_images(http: httpx.Client, items: list, img_dir: Path,
                              force: bool) -> tuple[list[str], int, int]:
    """Download missing snapshot JPEGs. Returns (names relative to the output dir, fetched, failed)."""
    img_dir.mkdir(parents=True, exist_ok=True)
    names = []
    fetched = failed = 0
    for i, s in enumerate(items, 1):
        name = f"{i:03}_{clock(s.seek_seconds).replace(':', '-')}.jpg"
        names.append(f"{img_dir.name}/{name}")
        dest = img_dir / name
        if not s.image_url or (dest.exists() and not force):
            continue
        try:
            resp = http.get(s.image_url)
            resp.raise_for_status()
            _write(dest, resp.content)
            fetched += 1
        except httpx.HTTPError as e:
            failed += 1
            err.print(f"[yellow]  snapshot {i}: {e}[/yellow]")
    return names, fetched, failed


# -- course files ----------------------------------------------------------


def _file_states(c: CanvasClient, crs: Course, cfg: Optional[CourseConfig]) -> tuple[Manifest, list[FileState]]:
    """Fetch the file index, mark copies already in the course folder as local, return states."""
    files = c.course_files(crs.id)
    manifest = _manifest(crs.id)
    if cfg:
        reconcile_local(manifest, files, cfg.dir)
    return manifest, sorted((manifest.state(f) for f in files), key=lambda s: s.file.name)


def _filter_states(states: list[FileState], match: Optional[str], since: Optional[str]) -> list[FileState]:
    if match:
        states = [s for s in states if match.lower() in s.file.name.lower()]
    if since:
        states = [s for s in states if s.file.updated_at[:10] >= since]
    return states


MatchOpt = typer.Option(None, "--match", "-m", help="Only files whose name contains this text")
FileSinceOpt = typer.Option(None, "--since", callback=_check_date, help="Only files updated on or after YYYY-MM-DD")
STATUS_LABEL = {"new": "新", "pending": "未下载", "downloaded": "已下载", "local": "本地已有",
                "updated": "有更新", "locked": "未开放"}


@app.command()
def files(
    course: str = CourseArg,
    match: Optional[str] = MatchOpt,
    since: Optional[str] = FileSinceOpt,
    pending: bool = typer.Option(False, "--pending", help="Only files you don't have (new, not downloaded, updated)"),
    as_json: bool = JsonOpt,
) -> None:
    """List course files with where they appear on Canvas and whether you have them."""
    with _client() as c:
        try:
            crs, cfg = _resolve(c, course)
            manifest, states = _file_states(c, crs, cfg)
        except REQUEST_ERRORS as e:
            _fail(e)
        manifest.save()
    states = _filter_states(states, match, since)
    if pending:
        states = [s for s in states if s.status in ("new", "pending", "updated")]
    if as_json:
        _emit_json([s.to_dict() for s in states])
        return
    t = Table("id", "name", "size", "updated", "source", "status", title=f"{crs.name} — {len(states)} files")
    for s in states:
        t.add_row(str(s.file.id), s.file.name, _human_size(s.file.size), s.file.updated_at[:10],
                  s.file.source, STATUS_LABEL.get(s.status, s.status))
    out.print(t)


def _download_states(c: CanvasClient, manifest: Manifest, chosen: list[FileState], target: Path,
                     dry_run: bool, force: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    taken: set[str] = set()
    plan: list[tuple[FileState, Path]] = []
    for s in chosen:
        entry = s.to_dict()
        if s.status == "locked":
            results.append({**entry, "status": "locked"})
        elif s.status in ("downloaded", "local") and not force:
            results.append({**entry, "status": "exists"})
        else:
            dest = destination(s.file, target, manifest, taken)
            plan.append((s, dest))
    if dry_run:
        return results + [{**s.to_dict(), "dest": str(d), "status": "would-download"} for s, d in plan]
    if not plan:
        return results
    with Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                  console=err, transient=True) as progress:
        for s, dest in plan:
            f = s.file
            task = progress.add_task(f.name[:40], total=f.size or None)
            entry = {**s.to_dict(), "dest": str(dest)}
            try:
                c.download(f.url, dest, lambda n, t=task: progress.advance(t, n), f.modified_at)
                manifest.mark_downloaded(f, dest)
                entry["status"] = "downloaded"
                err.print(f"[green]+ {f.name}[/green] ({_human_size(f.size)})")
            except (CanvasError, httpx.HTTPError, OSError) as e:
                entry.update(status="error", error=str(e))
                err.print(f"[yellow]! {f.name}: {e}[/yellow]")
            progress.remove_task(task)
            results.append(entry)
    return results


@app.command()
def download(
    course: str = CourseArg,
    out_dir: Optional[Path] = typer.Option(
        None, "--out", "-o", help="Target directory (default: <dir>/<slides> from config.toml, else .)",
        file_okay=False),
    file_ids: Optional[list[int]] = typer.Option(None, "--id", help="File id (repeatable), see `canvaskit files`"),
    new: bool = typer.Option(False, "--new", help="Files listed by the most recent `sync` that reported something"),
    match: Optional[str] = MatchOpt,
    since: Optional[str] = FileSinceOpt,
    all_files: bool = typer.Option(False, "--all", help="Every file you don't have yet"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be downloaded"),
    force: bool = ForceOpt,
    as_json: bool = JsonOpt,
) -> None:
    """Download course files into one flat folder. Choose files with --id, --new, -m or --all."""
    if not (file_ids or new or match or since or all_files):
        raise typer.BadParameter("choose files with --id, --new, --match, --since or --all")
    with _client() as c:
        try:
            crs, cfg = _resolve(c, course)
            manifest, states = _file_states(c, crs, cfg)
        except REQUEST_ERRORS as e:
            _fail(e)
        target = _out_dir(out_dir, cfg.slides_dir if cfg else None)
        chosen = states
        if file_ids:
            wanted = set(file_ids)
            chosen = [s for s in chosen if s.file.id in wanted]
            missing = wanted - {s.file.id for s in chosen}
            if missing:
                err.print(f"[yellow]unknown file id(s): {', '.join(map(str, sorted(missing)))}[/yellow]")
        if new:
            recent = set(manifest.last_new)
            chosen = [s for s in chosen if s.file.id in recent or s.status == "updated"]
        chosen = _filter_states(chosen, match, since)
        err.print(f"[dim]{crs.name} (course {crs.id}) → {target}[/dim]")
        results = _download_states(c, manifest, chosen, target, dry_run, force)
        if not dry_run:
            manifest.save()
    if as_json:
        _emit_json(results)
        return
    if dry_run:
        for r in results:
            if r["status"] == "would-download":
                out.print(f"would download {r['name']} ({_human_size(r['size'])}) → {r['dest']}")
    out.print(_count(results) or "No files matched.")


# -- sync ------------------------------------------------------------------


def _sync_course(c: CanvasClient, cfg: CourseConfig, do_subtitles: bool, do_files: bool) -> dict[str, Any]:
    crs = c.resolve_course(str(cfg.canvas_id))
    report: dict[str, Any] = {"course": cfg.key, "course_id": crs.id, "name": crs.name, "dir": str(cfg.dir)}
    if do_subtitles:
        try:
            v, items = _lectures_for(c, crs)
            with v:
                report["subtitles"] = _fetch_subtitles(v, items, cfg.subtitles_dir, cfg.formats, False)
        except REQUEST_ERRORS as e:
            report["subtitles_error"] = str(e)
            err.print(f"[yellow]! {crs.name} subtitles: {e}[/yellow]")
    if do_files:
        # Reported separately so subtitles fetched above still show up when the file check fails.
        try:
            report.update(_sync_files(c, crs, cfg))
        except REQUEST_ERRORS as e:
            report["files_error"] = str(e)
            err.print(f"[yellow]! {crs.name} files: {e}[/yellow]")
    return report


def _sync_files(c: CanvasClient, crs: Course, cfg: CourseConfig) -> dict[str, Any]:
    manifest = _manifest(crs.id)
    first_sync = manifest.last_sync is None
    files_ = c.course_files(crs.id)
    local = reconcile_local(manifest, files_, cfg.dir)
    states = [manifest.state(f) for f in files_]
    # Keep Canvas order (modules in course order, then the Files listing, then links).
    new = [s for s in states if s.status == "new"]
    updated = [s for s in states if s.status == "updated"]
    for s in new:
        manifest.mark_seen(s.file)
    if new or updated:  # an empty sync keeps the previous list for `download --new`
        manifest.last_new = [s.file.id for s in new + updated]
    manifest.last_sync = now()
    manifest.save()
    return {
        "first_sync": first_sync,
        "new_files": [s.to_dict() for s in new],
        "updated_files": [s.to_dict() for s in updated],
        "found_locally": len(local),
    }


def _print_sync(r: dict[str, Any]) -> None:
    out.rule(f"{r['name']}  [{r['course']}]")
    subs = r.get("subtitles")
    if subs is not None:
        got = [s for s in subs if s["status"] == "downloaded"]
        waiting = [s for s in subs if s["status"] in ("unavailable", "error")]
        line = f"字幕：新下载 {len(got)} 节"
        if got:
            names = [s["lecture"]["title"].split(" ", 1)[-1] for s in got]
            line += "（" + "、".join(names[-4:]) + (f" 等 {len(names)} 节" if len(names) > 4 else "") + "）"
        if waiting:
            line += f"，暂无 {len(waiting)} 节"
        out.print(line)
    elif r.get("subtitles_error"):
        out.print(f"字幕：[yellow]{r['subtitles_error']}[/yellow]")
    if r.get("files_error"):
        out.print(f"文件：[yellow]{r['files_error']}[/yellow]")
    if "new_files" not in r:
        return
    if r.get("found_locally"):
        out.print(f"[dim]本地已有 {r['found_locally']} 个文件（同名同大小），已记为已有[/dim]")
    new, updated = r["new_files"], r["updated_files"]
    if not new and not updated:
        out.print("文件：没有新文件")
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in new:
        groups.setdefault(f["source"], []).append(f)
    out.print(f"文件：新 {len(new)} 个" + (f"，有更新 {len(updated)} 个" if updated else ""))
    limit = 12
    for source, items in groups.items():
        out.print(f"  [bold]{source}[/bold]  ({len(items)})")
        for f in items[:limit]:
            lock = "  [未开放]" if f["locked"] else ""
            out.print(f"    {f['id']:>9}  {f['name']}  [dim]{_human_size(f['size'])}[/dim]{lock}")
        if len(items) > limit:
            out.print(f"    [dim]… 另有 {len(items) - limit} 个，`canvaskit files {r['course']} --pending` 查看全部[/dim]")
    for f in updated:
        out.print(f"  [bold]有更新[/bold]  {f['id']:>9}  {f['name']}  [dim]Canvas 上 {f['updated_at'][:10]} 更新[/dim]")
    out.print(f"  → 下载：canvaskit download {r['course']} --new   或   --id <id> ...")


@app.command()
def sync(
    course: Optional[list[str]] = typer.Argument(None, help="Configured course keys (default: all in config.toml)"),
    no_subtitles: bool = typer.Option(False, "--no-subtitles", help="Skip subtitles"),
    no_files: bool = typer.Option(False, "--no-files", help="Skip the file check"),
    as_json: bool = JsonOpt,
) -> None:
    """Download new subtitles and list new or updated course files (files are not downloaded)."""
    configured = _courses_config()
    if not configured:
        err.print(f"[red]No courses configured. Add [courses.<name>] entries to {CONFIG_FILE}[/red]")
        raise typer.Exit(2)
    targets: list[CourseConfig] = []
    for q in course or list(configured):
        cfg = find_course_config(q, configured)
        if cfg is None:
            err.print(f"[red]'{q}' is not a configured course ({', '.join(configured)})[/red]")
            raise typer.Exit(2)
        targets.append(cfg)
    reports = []
    with _client() as c:
        for cfg in targets:
            try:
                reports.append(_sync_course(c, cfg, not no_subtitles, not no_files))
            except REQUEST_ERRORS as e:
                err.print(f"[yellow]! {cfg.key}: {e}[/yellow]")
                reports.append({"course": cfg.key, "course_id": cfg.canvas_id, "error": str(e)})
    if as_json:
        _emit_json(reports)
        return
    for r in reports:
        if "error" in r:
            out.rule(f"{r['course']}")
            out.print(f"[red]{r['error']}[/red]")
        else:
            _print_sync(r)


# -- skill -----------------------------------------------------------------

skill_app = typer.Typer(help="Install the bundled Claude Code skill.", no_args_is_help=True)
app.add_typer(skill_app, name="skill")
SkillsDirOpt = typer.Option(None, "--target", help="Skills directory (default: ~/.claude/skills)", file_okay=False)
SKILL_STATUS = {
    "installed": "已安装",
    "updated": "已更新",
    "unchanged": "已是最新",
    "exists": "已存在不同版本或链接，未改动（--force 覆盖）",
    "removed": "已删除",
    "absent": "未安装",
}


def _skill_result(status: str, path: Path, as_json: bool) -> None:
    if as_json:
        _emit_json({"status": status, "path": str(path)})
    else:
        out.print(f"{SKILL_STATUS[status]}：{path}")


@skill_app.command("install")
def skill_install(
    target: Optional[Path] = SkillsDirOpt,
    force: bool = typer.Option(False, "--force", "-f", help="Replace a different version or a symlink"),
    as_json: bool = JsonOpt,
) -> None:
    """Copy SKILL.md to <target>/canvaskit/. Run again with --force after upgrading canvaskit."""
    try:
        status, path = skill.install(target, force)
    except (skill.SkillError, OSError) as e:
        _fail(e)
    _skill_result(status, path, as_json)


@skill_app.command("uninstall")
def skill_uninstall(target: Optional[Path] = SkillsDirOpt, as_json: bool = JsonOpt) -> None:
    """Remove <target>/canvaskit/."""
    try:
        status, path = skill.uninstall(target)
    except OSError as e:
        _fail(e)
    _skill_result(status, path, as_json)


@skill_app.command("path")
def skill_path() -> None:
    """Print the location of the bundled SKILL.md (to hand to other agents)."""
    try:
        sys.stdout.write(f"{skill.source()}\n")
    except skill.SkillError as e:
        _fail(e)


if __name__ == "__main__":
    app()
