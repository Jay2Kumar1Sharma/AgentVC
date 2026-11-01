"""The workspace and its `.agentvc/` store (ADR-0009, plan §6.1 "Self-ignoring state folder")."""

from __future__ import annotations

from pathlib import Path

from agentvc.io import IO

STORE_DIR = ".agentvc"
GITIGNORE = b"*\n"


def find_workspace(io: IO, start: Path, *, project_dir: str | None = None) -> Path:
    """`git rev-parse --show-toplevel` of the project folder, or that folder outside a repo.

    A linked worktree and a nested repository each have their own top level, so each is its
    own workspace (ADR-0009).
    """
    folder = Path(project_dir) if project_dir else start
    try:
        result = io.run(["git", "rev-parse", "--show-toplevel"], timeout=5, cwd=folder)
    except OSError:
        return folder.resolve()
    if result.returncode == 0 and not result.timed_out:
        top = result.stdout.decode("utf-8", "replace").strip()
        if top:
            return Path(top).resolve()
    return folder.resolve()


class Store:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.dir = workspace / STORE_DIR
        self.ledger = self.dir / "ledger.db"
        self.ledger_lock = self.dir / "ledger.lock"
        self.blindspots = self.dir / "blindspots.jsonl"
        self.debug_log = self.dir / "debug.log"
        self.daemon_state = self.dir / "daemon.json"
        self.daemon_log = self.dir / "daemon.log"
        self.config = self.dir / "config.toml"

    def exists(self) -> bool:
        return self.dir.is_dir()

    def ensure(self, io: IO) -> None:
        """Create the store; its `.gitignore` (`*`) is written before anything else."""
        io.mkdir(self.dir)
        ignore = self.dir / ".gitignore"
        try:
            current = io.read_bytes(ignore)
        except FileNotFoundError:
            current = None
        if current != GITIGNORE:
            io.write_atomic(ignore, GITIGNORE)
