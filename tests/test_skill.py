import json

from typer.testing import CliRunner

from canvaskit import cli, skill

runner = CliRunner()


def test_source_is_the_repository_skill():
    src = skill.source()
    assert src.name == "SKILL.md"
    assert src.read_text(encoding="utf-8").startswith("---\nname: canvaskit")


def test_install_lifecycle(tmp_path):
    dest = tmp_path / "canvaskit" / "SKILL.md"
    assert skill.install(tmp_path) == ("installed", dest)
    assert dest.read_bytes() == skill.source().read_bytes()
    assert skill.install(tmp_path) == ("unchanged", dest)

    dest.write_text("old version", encoding="utf-8")
    assert skill.install(tmp_path) == ("exists", dest)
    assert dest.read_text(encoding="utf-8") == "old version"  # left alone without --force
    assert skill.install(tmp_path, force=True) == ("updated", dest)
    assert dest.read_bytes() == skill.source().read_bytes()

    assert skill.uninstall(tmp_path) == ("removed", tmp_path / "canvaskit")
    assert not (tmp_path / "canvaskit").exists()
    assert skill.uninstall(tmp_path)[0] == "absent"


def test_install_keeps_a_developer_symlink_unless_forced(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "SKILL.md").write_text("dev copy", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "canvaskit").symlink_to(checkout)

    assert skill.install(skills)[0] == "exists"
    assert (skills / "canvaskit").is_symlink()
    assert skill.install(skills, force=True)[0] == "updated"
    assert not (skills / "canvaskit").is_symlink()
    assert (checkout / "SKILL.md").read_text(encoding="utf-8") == "dev copy"  # the checkout is untouched


def test_skill_commands_json(tmp_path):
    result = runner.invoke(cli.app, ["skill", "install", "--target", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "installed", "path": str(tmp_path / "canvaskit" / "SKILL.md")}

    result = runner.invoke(cli.app, ["skill", "uninstall", "--target", str(tmp_path), "--json"])
    assert json.loads(result.stdout)["status"] == "removed"

    result = runner.invoke(cli.app, ["skill", "path"])
    assert result.exit_code == 0 and result.stdout.strip().endswith("SKILL.md")
