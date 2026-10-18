"""Install the bundled Claude Code skill (SKILL.md) into ~/.claude/skills/canvaskit/.

The skill is copied rather than symlinked: a link would point into the tool's
install directory, which moves when the tool is reinstalled or upgraded.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

SKILL_NAME = "canvaskit"
DEFAULT_SKILLS_DIR = Path.home() / ".claude" / "skills"


class SkillError(Exception):
    pass


def source() -> Path:
    """SKILL.md shipped in the wheel, or the repository copy for editable / source installs."""
    packaged = resources.files("canvaskit") / "skill" / "SKILL.md"
    if packaged.is_file():
        return Path(str(packaged))
    repo = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME / "SKILL.md"
    if repo.is_file():
        return repo
    raise SkillError("SKILL.md is not bundled with this installation")


def install(skills_dir: Path | None = None, force: bool = False) -> tuple[str, Path]:
    """Return (status, path); status is installed | updated | unchanged | exists.

    `exists` means a different SKILL.md (or a symlink, e.g. a developer's checkout)
    is already there and was left alone because force is off.
    """
    target = (skills_dir or DEFAULT_SKILLS_DIR) / SKILL_NAME
    dest = target / "SKILL.md"
    content = source().read_bytes()
    status = "installed"
    if target.is_symlink():
        if not force:
            return "exists", dest
        target.unlink()
        status = "updated"
    elif dest.is_file():
        if dest.read_bytes() == content:
            return "unchanged", dest
        if not force:
            return "exists", dest
        status = "updated"
    target.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)
    return status, dest


def uninstall(skills_dir: Path | None = None) -> tuple[str, Path]:
    """Return (status, path); status is removed | absent."""
    target = (skills_dir or DEFAULT_SKILLS_DIR) / SKILL_NAME
    if target.is_symlink() or target.is_file():
        target.unlink()
        return "removed", target
    if target.is_dir():
        shutil.rmtree(target)
        return "removed", target
    return "absent", target
