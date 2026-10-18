"""Configuration.

Credentials: environment first, then ~/.config/canvaskit/env. The env file uses
shell syntax (`export KEY=value`) so it can also be sourced from ~/.zshrc;
reading it directly keeps non-interactive shells working.

Courses: ~/.config/canvaskit/config.toml maps a short name to a Canvas course
and a local folder, e.g.

    [courses."高数"]          # non-ASCII keys must be quoted
    canvas_id = 12345
    dir = "~/Documents/高等数学"
    subtitles = "课程字幕"        # default "subtitles"
    slides = "slides"             # default "slides"
    subtitle_format = "srt,txt"   # default "srt,txt"
"""

from __future__ import annotations

import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_DIR = Path(os.environ.get("CANVASKIT_CONFIG_DIR", Path.home() / ".config" / "canvaskit"))
ENV_FILE = CONFIG_DIR / "env"
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_DIR = CONFIG_DIR / "state"
DEFAULT_BASE_URL = "https://oc.sjtu.edu.cn"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    token: str
    base_url: str


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    # utf-8-sig: Windows PowerShell 5 writes a BOM, which would otherwise end up in the first key.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, raw = line.partition("=")
        if not sep:
            continue
        parts = shlex.split(raw) if raw else [""]
        values[key.strip()] = parts[0] if parts else ""
    return values


def load_config() -> Config:
    file_values = _read_env_file(ENV_FILE)
    token = os.environ.get("CANVAS_TOKEN") or file_values.get("CANVAS_TOKEN", "")
    base_url = os.environ.get("CANVAS_BASE_URL") or file_values.get("CANVAS_BASE_URL") or DEFAULT_BASE_URL
    if not token:
        raise ConfigError(
            f"Canvas token not found. Set CANVAS_TOKEN or write `export CANVAS_TOKEN=...` to {ENV_FILE}"
        )
    return Config(token=token, base_url=base_url.rstrip("/"))


@dataclass(frozen=True)
class CourseConfig:
    key: str
    canvas_id: int
    dir: Path
    subtitles: str = "subtitles"
    slides: str = "slides"
    subtitle_format: str = "srt,txt"

    @property
    def subtitles_dir(self) -> Path:
        return self.dir / self.subtitles

    @property
    def slides_dir(self) -> Path:
        return self.dir / self.slides

    @property
    def formats(self) -> list[str]:
        return [f.strip() for f in self.subtitle_format.split(",") if f.strip()]


def load_courses(path: Path | None = None) -> dict[str, CourseConfig]:
    path = path or CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    courses = {}
    for key, raw in (data.get("courses") or {}).items():
        if not isinstance(raw, dict) or "canvas_id" not in raw or "dir" not in raw:
            raise ConfigError(f"{path}: [courses.{key}] needs canvas_id and dir")
        courses[key] = CourseConfig(
            key=key,
            canvas_id=int(raw["canvas_id"]),
            dir=Path(os.path.expanduser(str(raw["dir"]))),
            subtitles=str(raw.get("subtitles", "subtitles")),
            slides=str(raw.get("slides", "slides")),
            subtitle_format=str(raw.get("subtitle_format", "srt,txt")),
        )
    return courses


def find_course_config(query: str, courses: dict[str, CourseConfig]) -> CourseConfig | None:
    """Match a configured course by key, canvas id, part of a key, or a key inside the query.

    Tried in that order; the first rule with any hits decides, and only a unique hit counts,
    so a short key cannot shadow a closer match."""
    if query in courses:
        return courses[query]
    rules = (
        lambda c: query == str(c.canvas_id),
        lambda c: query in c.key,  # 高 -> 高数
        lambda c: c.key in query,  # 高数第三章 -> 高数
    )
    for rule in rules:
        hits = [c for c in courses.values() if rule(c)]
        if hits:
            return hits[0] if len(hits) == 1 else None
    return None
