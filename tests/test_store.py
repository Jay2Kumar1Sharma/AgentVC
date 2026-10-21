"""agentvc/store.py: workspace root (ADR-0009) and the self-ignoring `.agentvc/` folder."""

import subprocess
from pathlib import Path

from agentvc.io import IO
from agentvc.store import Store, find_workspace


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=path)
    return path


def same(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def test_workspace_is_the_git_top_level_of_a_subfolder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "mono")
    sub = repo / "packages" / "app"
    sub.mkdir(parents=True)
    assert same(find_workspace(IO(), sub), repo)


def test_project_dir_wins_over_the_current_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert same(find_workspace(IO(), elsewhere, project_dir=str(repo)), repo)


def test_a_folder_outside_any_repository_is_its_own_workspace(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert same(find_workspace(IO(), plain), plain)


def test_a_nested_repository_is_its_own_workspace(tmp_path: Path) -> None:
    outer = make_repo(tmp_path / "outer")
    nested = make_repo(outer / "vendor" / "nested")
    assert same(find_workspace(IO(), nested), nested)


def test_store_ignores_itself_before_anything_is_written(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj")
    store = Store(repo)
    assert not store.exists()
    store.ensure(IO())
    assert store.exists()
    assert (store.dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    (store.dir / "ledger.db").write_bytes(b"secret recording")
    assert git("status", "--porcelain", "--untracked-files=all", cwd=repo) == ""


def test_ensure_repairs_a_changed_gitignore(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.ensure(IO())
    (store.dir / ".gitignore").write_text("!ledger.db\n", encoding="utf-8")
    store.ensure(IO())
    assert (store.dir / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_store_paths(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.dir == tmp_path / ".agentvc"
    assert store.ledger == store.dir / "ledger.db"
    assert store.blindspots == store.dir / "blindspots.jsonl"
    assert store.debug_log == store.dir / "debug.log"
    assert store.daemon_state == store.dir / "daemon.json"
    assert store.config == store.dir / "config.toml"
