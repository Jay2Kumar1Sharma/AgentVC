"""The `agentvc` command line (design note cli.md).

User commands: history, inspect, check. Internal ones, called by the hooks: hook, daemon,
hooks-config.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentvc import __version__
from agentvc.clock import SystemClock, SystemRandom
from agentvc.config import load_config
from agentvc.debuglog import enabled_from_env
from agentvc.hook import HookContext, agentvc_home, handle
from agentvc.io import IO
from agentvc.store import Store, find_workspace


def _payload_cwd(body: bytes) -> str | None:
    try:
        cwd = json.loads(body).get("cwd")
    except (ValueError, AttributeError, RecursionError):
        return None
    return cwd if isinstance(cwd, str) and cwd else None


def _hook_workspace(io: IO, body: bytes, environ: Mapping[str, str]) -> Path:
    project = environ.get("CLAUDE_PROJECT_DIR") or None
    start = Path(_payload_cwd(body) or os.getcwd())
    if not project and not start.is_dir():
        start = Path(os.getcwd())
    return find_workspace(io, start, project_dir=project)


def cmd_hook(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    body = sys.stdin.buffer.read()
    io, clock = IO(), SystemClock()
    workspace = _hook_workspace(io, body, environ)
    if args.ensure_daemon:
        from agentvc.daemon import run_liveness

        reply = run_liveness(
            body,
            workspace=workspace,
            io=io,
            clock=clock,
            rand=SystemRandom(),
            environ=environ,
            python=sys.executable,
        )
    else:
        store = Store(workspace)
        ctx = HookContext(
            io=io,
            clock=clock,
            workspace=workspace,
            config=load_config(io, store.config),
            anchor_dir=agentvc_home(dict(environ)) / "anchors",
            debug_enabled=enabled_from_env(environ),
        )
        try:
            reply = handle(body, ctx)
        finally:
            ctx.close()
    code, stdout, stderr = reply.to_command()
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return code


def cmd_daemon(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from agentvc.daemon import run_daemon

    return run_daemon(Path(args.workspace).resolve(), environ, IO(), SystemClock(), SystemRandom())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentvc", description="Version control for what AI coding agents do."
    )
    parser.add_argument("--version", action="version", version=f"agentvc {__version__}")
    sub = parser.add_subparsers(dest="command")

    hook = sub.add_parser("hook", help="(internal) handle one hook event from stdin")
    hook.add_argument(
        "--ensure-daemon", action="store_true", help="start agentvcd if needed, then forward"
    )
    hook.set_defaults(func=cmd_hook)

    daemon = sub.add_parser("daemon", help="(internal) run agentvcd for a workspace")
    daemon.add_argument("--workspace", required=True)
    daemon.set_defaults(func=cmd_daemon)
    return parser


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        print(f"agentvc {__version__}")
        return 0
    code: int = args.func(args, os.environ if environ is None else environ)
    return code
