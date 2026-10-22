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


# --- session 4: version and [snapshot] (plan §14.1, ADR-0006, ADR-0010) --------------------------


def test_snapshot_defaults_follow_adr_0006_and_0010(tmp_path: Path) -> None:
    snap = load_config(IO(), tmp_path / "config.toml").snapshot
    assert (snap.time_budget_ms, snap.max_file_mb) == (2000, 100)
    assert (snap.ignored_file_max_kb, snap.ignored_total_mb) == (1024, 50)
    assert {"node_modules", ".venv", "target", "dist", "build", "__pycache__"} <= set(
        snap.heavy_folders
    )
    assert snap.always_include == () and snap.never_include == ()


def test_snapshot_settings_are_read(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "version = 1\n"
        'mode = "observe"\n'
        "[snapshot]\n"
        "time_budget_ms = 500\n"
        "max_file_mb = 10\n"
        "ignored_file_max_kb = 64\n"
        "ignored_total_mb = 5\n"
        'heavy_folders = ["vendor"]\n'
        'always_include = ["local/*.db"]\n'
        'never_include = ["data/**"]\n',
        encoding="utf-8",
    )
    config = load_config(IO(), path)
    assert config.problems == ()
    assert config.mode == "observe"
    snap = config.snapshot
    assert (snap.time_budget_ms, snap.max_file_mb) == (500, 10)
    assert (snap.ignored_file_max_kb, snap.ignored_total_mb) == (64, 5)
    assert snap.heavy_folders == ("vendor",)
    assert snap.always_include == ("local/*.db",)
    assert snap.never_include == ("data/**",)


def test_each_bad_setting_is_reported_and_the_rest_still_apply(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'mode = "strict"\n'
        "colour = true\n"
        "[snapshot]\n"
        "time_budget_ms = -5\n"
        "max_file_mb = true\n"
        'heavy_folders = "node_modules"\n'
        "never_include = [1, 2]\n"
        "ignored_total_mb = 7\n"
        "budget = 3\n",
        encoding="utf-8",
    )
    config = load_config(IO(), path)
    assert config.mode == "strict"
    assert config.snapshot.ignored_total_mb == 7
    assert config.snapshot.time_budget_ms == 2000 and config.snapshot.max_file_mb == 100
    assert "node_modules" in config.snapshot.heavy_folders
    assert config.snapshot.never_include == ()
    text = " | ".join(config.problems)
    for name in ("colour", "time_budget_ms", "max_file_mb", "heavy_folders", "never_include"):
        assert name in text
    assert "budget" in text
    assert len(config.problems) == 6


def test_a_newer_config_version_is_reported_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('version = 2\nmode = "observe"\nfuture_setting = 1\n', encoding="utf-8")
    config = load_config(IO(), path)
    assert config.mode == "observe"
    assert any("newer" in p for p in config.problems)
    assert not any("future_setting" in p for p in config.problems)
