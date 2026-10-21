"""agentvc/config.py: minimal config (mode, record_prompts); a bad file never turns AgentVC off."""

from pathlib import Path

from agentvc.config import load_config
from agentvc.io import IO


def test_missing_file_gives_defaults_without_problems(tmp_path: Path) -> None:
    config = load_config(IO(), tmp_path / "config.toml")
    assert config.mode == "protect"
    assert config.record_prompts is False
    assert config.problems == ()


def test_values_are_read(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mode = "strict"\nrecord_prompts = true\n', encoding="utf-8")
    config = load_config(IO(), path)
    assert (config.mode, config.record_prompts, config.problems) == ("strict", True, ())


def test_invalid_toml_is_reported_and_defaults_apply(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("mode = = observe", encoding="utf-8")
    config = load_config(IO(), path)
    assert config.mode == "protect"
    assert config.problems and "config.toml" in config.problems[0]


def test_unknown_mode_and_wrong_types_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('mode = "off"\nrecord_prompts = "yes"\n', encoding="utf-8")
    config = load_config(IO(), path)
    assert config.mode == "protect"
    assert config.record_prompts is False
    assert len(config.problems) == 2


def test_unreadable_file_is_reported(tmp_path: Path) -> None:
    import errno

    path = tmp_path / "config.toml"
    path.write_text('mode = "observe"\n', encoding="utf-8")
    io = IO()
    io.faults.fail("read", PermissionError(errno.EACCES, "denied"))
    config = load_config(io, path)
    assert config.mode == "protect"
    assert config.problems
