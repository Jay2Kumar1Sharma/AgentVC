"""The `agentvc` command line (design note cli.md).

User commands: history, inspect, check. Internal ones, called by the hooks: hook, daemon,
hooks-config.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentvc import __version__, blindspots, wiring
from agentvc.clock import SystemClock, SystemRandom
from agentvc.config import load_config
from agentvc.debuglog import enabled_from_env
from agentvc.hook import HookContext, agentvc_home, handle
from agentvc.io import IO
from agentvc.ledger import Ledger, LedgerError, Verdict
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


# --- reading the ledger ---------------------------------------------------------------------------

GAP_TEXT = {
    "post-missing": "no post event (the tool may have run unrecorded)",
    "pre-missing": "no pre event (state before it unknown)",
    "pre-late": "pre event came after the post (state before it unknown)",
    "reconciled-from-batch": "seen only in the batch summary",
    "hook-error": "AgentVC failed while recording",
    "hook-timeout": "AgentVC ran out of time while recording",
    "daemon-down": "AgentVC's background process was not running",
}


class _Reading:
    """The workspace's store and ledger, opened for a read command."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self.io = IO()
        self.workspace = find_workspace(self.io, Path.cwd())
        if not Store(self.workspace).ledger.exists():
            # Outside a git repository the workspace is the folder the session started in;
            # from a subfolder of it, find it by its store.
            here = Path.cwd().resolve()
            found = next((p for p in (here, *here.parents) if Store(p).ledger.exists()), None)
            self.workspace = found or self.workspace
        self.store = Store(self.workspace)
        self.ledger: Ledger | None = None
        if self.store.ledger.exists():
            self.ledger = Ledger.open(
                self.io,
                self.store.ledger,
                workspace=self.workspace,
                anchor_dir=agentvc_home(dict(environ)) / "anchors",
                clock=SystemClock(),
            )

    def close(self) -> None:
        if self.ledger is not None:
            self.ledger.close()


def _flags(row: Mapping[str, object]) -> list[str]:
    value = json.loads(str(row.get("flags") or "[]"))
    return [str(v) for v in value] if isinstance(value, list) else []


def _verdict_json(verdict: Verdict) -> dict[str, object]:
    return {
        "status": verdict.status,
        "problems": list(verdict.problems),
        "head_seq": verdict.head_seq,
    }


def _verdict_lines(verdict: Verdict) -> list[str]:
    if verdict.status == "ok":
        return ["Ledger: ok"]
    if verdict.status == "anchor-behind":
        return [f"Ledger: ok ({verdict.problems[0]})"]
    return [f"Ledger: {verdict.status.upper()}", *(f"  - {p}" for p in verdict.problems)]


def _run_summaries(ledger: Ledger, include_checks: bool) -> list[dict[str, object]]:
    out = []
    steps = ledger.rows("steps")
    turns = ledger.rows("turns")
    for run in reversed(ledger.rows("runs")):
        flags = _flags(run)
        if "check" in flags and not include_checks:
            continue
        own = [s for s in steps if s["run_id"] == run["id"]]
        out.append(
            {
                "id": run["id"],
                "agent": run["agent"],
                "started_at": run["started_at"],
                "ended_at": run["ended_at"],
                "status": run["status"],
                "flags": flags,
                "turns": sum(1 for t in turns if t["run_id"] == run["id"]),
                "steps": len(own),
                "gaps": sum(1 for s in own if s["status"] == "GAP"),
                "denied": sum(1 for s in own if s["status"] == "DENIED"),
            }
        )
    return out


def _when(iso: object, *, time_only: bool = False) -> str:
    text = str(iso or "")
    if len(text) < 19:
        return text
    return text[11:19] if time_only else text[:10] + " " + text[11:19]


def _not_recorded(reading: _Reading, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"workspace": str(reading.workspace), "runs": [], "verdict": None}))
    else:
        print(f"AgentVC has not recorded anything in {reading.workspace}.")
    return 0


def cmd_history(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    reading = _Reading(environ)
    try:
        if reading.ledger is None:
            return _not_recorded(reading, args.json)
        runs = _run_summaries(reading.ledger, args.all)
        verdict = reading.ledger.verify()
        blind = blindspots.count(reading.io, reading.store.blindspots)
    finally:
        reading.close()
    if args.json:
        data = {
            "workspace": str(reading.workspace),
            "runs": runs,
            "verdict": _verdict_json(verdict),
            "blindspots": blind,
        }
        print(json.dumps(data, indent=1))
        return 0 if verdict.ok else 1
    print("Run  Agent        Started (UTC)        Status  Turns  Steps  Gaps")
    for r in runs:
        flags = ("  " + ", ".join(r["flags"])) if r["flags"] else ""  # type: ignore[arg-type]
        print(
            f"{r['id']:>3}  {r['agent']!s:<11}  {_when(r['started_at']):<19}  {r['status']!s:<6}"
            f"  {r['turns']:>5}  {r['steps']:>5}  {r['gaps']:>4}{flags}"
        )
    print()
    if blind:
        print(
            f"Blind spots: {blind} hook payload{'s' if blind != 1 else ''} couldn't be read "
            "(.agentvc/blindspots.jsonl)"
        )
    print("\n".join(_verdict_lines(verdict)))
    return 0 if verdict.ok else 1


def _summary(step: Mapping[str, object], workspace: Path) -> str:
    try:
        data = json.loads(str(step.get("input") or "null"))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("command"), str):
        text = " ".join(str(data["command"]).split())
    else:
        path = next(
            (
                data[k]
                for k in ("file_path", "notebook_path", "path")
                if isinstance(data.get(k), str)
            ),
            None,
        )
        if path is None:
            text = ", ".join(sorted(data))
        else:
            try:
                text = str(Path(path).relative_to(workspace))
            except ValueError:
                text = path
    return text if len(text) <= 34 else "..." + text[-31:]


def cmd_inspect(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    reading = _Reading(environ)
    try:
        if reading.ledger is None:
            return _not_recorded(reading, args.json)
        runs = reading.ledger.rows("runs")
        if args.run == "latest":
            visible = [r for r in runs if "check" not in _flags(r)]
            run = visible[-1] if visible else None
        else:
            run = next((r for r in runs if str(r["id"]) == args.run), None)
        if run is None:
            print(f"agentvc: no run {args.run} in {reading.workspace}")
            return 1
        steps = reading.ledger.rows("steps", "where run_id = ?", (run["id"],))
        turns = {t["id"]: t for t in reading.ledger.rows("turns", "where run_id = ?", (run["id"],))}
        verdict = reading.ledger.verify()
    finally:
        reading.close()
    if args.json:
        for s in steps:
            s["flags"] = _flags(s)
            s["input"] = json.loads(str(s["input"])) if s["input"] else None
        run = {**run, "flags": _flags(run)}
        data = {"run": run, "steps": steps, "verdict": _verdict_json(verdict)}
        print(json.dumps(data, indent=1))
        return 0 if verdict.ok else 1

    flags = _flags(run)
    head = (
        f"Run {run['id']} | {run['agent']} | started {_when(run['started_at'])} | {run['status']}"
    )
    print(head + (f" | flags: {', '.join(flags)}" if flags else ""))
    current_turn: object = object()
    for s in steps:
        if s["turn_id"] != current_turn:
            current_turn = s["turn_id"]
            turn = turns.get(s["turn_id"])
            print(f"Turn {turn['seq']}" if turn else "Before the first prompt")
        line = (
            f"  #{s['seq']:<2} {_when(s['started_at'], time_only=True)}  {s['tool'] or '-':<5}"
            f"  {s['category'] or '-':<5}  {_summary(s, reading.workspace):<34}"
        )
        outcome = s["outcome"] or ""
        if s["status"] == "RECORDED" or outcome:
            line += f"  {outcome:<5}"
            if s["duration_ms"] is not None:
                line += f"  {s['duration_ms']} ms"
        if s["status"] == "GAP":
            line += f"  GAP: {GAP_TEXT.get(str(s['gap_reason']), s['gap_reason'])}"
        elif s["status"] == "DENIED":
            line += f"  DENIED: {s['error']}"
        elif s["status"] == "PENDING":
            line += "  running (no post event yet)"
        step_flags = [f for f in _flags(s) if f not in ("pre-late",)]
        if step_flags:
            line += f"  [{', '.join(step_flags)}]"
        print(line.rstrip())
    counts = {k: sum(1 for s in steps if s["status"] == k) for k in ("RECORDED", "GAP", "DENIED")}
    print()
    print(
        f"Steps: {len(steps)} | recorded {counts['RECORDED']} | gaps {counts['GAP']}"
        f" | denied {counts['DENIED']}"
    )
    print("\n".join(_verdict_lines(verdict)))
    return 0 if verdict.ok else 1


def cmd_check(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from agentvc.check import render, run_check, to_json

    io = IO()
    workspace = find_workspace(io, Path.cwd())
    report = run_check(
        workspace,
        io=io,
        clock=SystemClock(),
        rand=SystemRandom(),
        environ=environ,
        python=sys.executable,
    )
    print(json.dumps(to_json(report), indent=1) if args.json else render(report))
    return 0 if report.ok else 1


def cmd_hooks_config(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    from agentvc.daemon import ensure_state

    io = IO()
    store = Store(find_workspace(io, Path.cwd()))
    store.ensure(io)
    state = ensure_state(io, store, SystemRandom())
    try:
        generated = wiring.claude_code_hooks(
            agent_version=args.agent_version,
            python=Path(sys.executable).resolve(),
            port=state.port,
            token=state.token,
            mode=args.mode or load_config(io, store.config).mode,
        )
    except ValueError as exc:
        print(f"agentvc: {exc}", file=sys.stderr)
        return 1
    for warning in generated.warnings:
        print(f"agentvc: {warning}", file=sys.stderr)
    print(json.dumps(generated.settings, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentvc", description="Version control for what AI coding agents do."
    )
    parser.add_argument("--version", action="version", version=f"agentvc {__version__}")
    sub = parser.add_subparsers(dest="command")

    history = sub.add_parser("history", help="list recorded runs, newest first")
    history.add_argument("--json", action="store_true", help="machine-readable output")
    history.add_argument("--all", action="store_true", help="include runs made by `check`")
    history.set_defaults(func=cmd_history)

    inspect = sub.add_parser("inspect", help="show one run's steps, gaps and outcomes")
    inspect.add_argument("run", help="run number, or `latest`")
    inspect.add_argument("--json", action="store_true", help="machine-readable output")
    inspect.set_defaults(func=cmd_inspect)

    check = sub.add_parser("check", help="prove recording works here, through the real hook path")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.set_defaults(func=cmd_check)

    config = sub.add_parser("hooks-config", help="print the Claude Code hook settings to install")
    config.add_argument("--agent-version", required=True, help="Claude Code version, e.g. 2.1.302")
    config.add_argument("--mode", choices=wiring.MODES, help="default: the configured mode")
    config.set_defaults(func=cmd_hooks_config)

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
    for stream in (sys.stdout, sys.stderr):
        # Piped output under -I uses the console code page; recorded commands and paths can
        # hold characters it lacks. Show them as "?" rather than crash (JSON output is ASCII).
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        print(f"agentvc {__version__}")
        return 0
    try:
        code: int = args.func(args, os.environ if environ is None else environ)
    except LedgerError as exc:  # too new or corrupt: say so, never guess (ADR-0007)
        print(f"agentvc: {exc}")
        return 1
    return code
