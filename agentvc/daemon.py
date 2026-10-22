"""`agentvcd`: the background process that receives `http` hooks (ADR-0003, hook-path.md).

One per workspace, bound to 127.0.0.1 only, on a port and with a token kept in
`.agentvc/daemon.json` for good (the generated hook settings name both). Requests need the token
and a loopback `Host`. Liveness runs once per prompt: `run_liveness` (the `SessionStart` /
`UserPromptSubmit` command hook) starts the daemon when needed, records a `daemon-down` GAP when
it should have been running but wasn't, and forwards the event, whose reply carries notices for
the user (gaps, blind spots, rejected requests).
"""

from __future__ import annotations

import contextlib
import http.client
import http.server
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agentvc import blindspots, wiring
from agentvc.adapters import claude_code
from agentvc.clock import Clock, RandomSource
from agentvc.config import load_config
from agentvc.debuglog import enabled_from_env
from agentvc.events import Event, Kind
from agentvc.hook import HookContext, HookReply, agentvc_home, handle, handle_event
from agentvc.io import IO
from agentvc.store import Store

TOKEN_HEADER = wiring.TOKEN_HEADER
DOWN_HEADER = "X-AgentVC-Down-Since"
MAX_BODY_BYTES = claude_code.MAX_BODY_BYTES


@dataclass
class DaemonState:
    port: int
    token: str
    pid: int | None = None
    state: str = "new"  # new | running | stopped | stopped-open
    started_at: str | None = None


def load_state(io: IO, store: Store) -> DaemonState | None:
    try:
        data = json.loads(io.read_text(store.daemon_state))
        return DaemonState(**data)
    except (FileNotFoundError, ValueError, TypeError):
        return None


def save_state(io: IO, store: Store, state: DaemonState) -> None:
    io.write_atomic(store.daemon_state, json.dumps(asdict(state), indent=1).encode())


# Below every OS's ephemeral range (Linux from 32768, Windows and macOS from 49152). A client
# whose ephemeral source port equals the target port connects to itself when nothing listens
# there, so the daemon's port must never be one the OS hands out to clients.
PORT_RANGE = (20000, 32768)


def _free_port(rand: RandomSource) -> int:
    low, high = PORT_RANGE
    for _ in range(100):
        port = low + int(rand.token_hex(2), 16) % (high - low)
        if port_is_free(port):
            return port
    raise OSError(f"no free port on 127.0.0.1 between {low} and {high}")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def ensure_state(io: IO, store: Store, rand: RandomSource) -> DaemonState:
    """The workspace's port and token, created once and then kept."""
    state = load_state(io, store)
    if state is None:
        state = DaemonState(port=_free_port(rand), token=rand.token_hex(16))
        save_state(io, store, state)
    return state


# --- client side ------------------------------------------------------------------------------


def call(
    state: DaemonState,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 2.0,
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", state.port, timeout=timeout)
    try:
        all_headers = {"Content-Type": "application/json", TOKEN_HEADER: state.token}
        all_headers.update(headers or {})
        conn.request(method, path, body=body, headers=all_headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return response.status, data if isinstance(data, dict) else {}
    finally:
        conn.close()


def is_healthy(state: DaemonState, timeout: float = 1.0) -> bool:
    try:
        status, data = call(state, "GET", "/health", timeout=timeout)
    except (OSError, http.client.HTTPException):
        return False
    return status == 200 and data.get("ok") is True


def wait_healthy(state: DaemonState, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        # Short attempts: a refused connection can take about a second on Windows. Never probe
        # by binding the port, which could take it from the daemon that is starting.
        if is_healthy(state, timeout=0.25):
            return True
        time.sleep(0.05)
    return False


# --- server side ------------------------------------------------------------------------------


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR would let any other local socket bind this port too and take
    # over hook traffic (token included); bind exclusively there instead.
    allow_reuse_address = sys.platform != "win32"
    agentvc: Server

    def server_bind(self) -> None:
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _HTTPServer

    def _send(self, status: int, data: dict[str, Any] | None = None) -> None:
        body = json.dumps(data).encode() if data is not None else b""
        head = (
            f"HTTP/1.1 {status} {self.responses.get(status, ('',))[0]}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        )
        self.wfile.write(head.encode("ascii") + body)  # one write (U-18: Nagle vs delayed ACK)

    def _drain(self, limit: int = 1024 * 1024) -> None:
        # Read a refused request's body before answering. Closing first means a body still on
        # its way gets a reset, and on Windows the reset discards the client's unread answer.
        length = self.headers.get("Content-Length", "0")
        if not length.isdigit() or int(length) > limit:
            return
        left = int(length)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                return
            left -= len(chunk)

    def _allowed(self) -> bool:
        owner = self.server.agentvc
        if self.headers.get("Host") != f"127.0.0.1:{owner.state.port}":
            self._drain()
            owner.count_rejected()  # an agent client sending another Host must not be silent
            self.close_connection = True
            self._send(403)
            return False
        if self.headers.get(TOKEN_HEADER) != owner.state.token:
            self._drain()
            owner.count_rejected()
            self.close_connection = True
            self._send(401)
            return False
        owner.touch()
        return True

    def do_GET(self) -> None:
        if not self._allowed():
            return
        if urlsplit(self.path).path == "/health":
            owner = self.server.agentvc
            self._send(200, {"ok": True, "pid": os.getpid(), "workspace": str(owner.ctx.workspace)})
        else:
            self._send(404)

    def do_POST(self) -> None:
        if not self._allowed():
            return
        owner = self.server.agentvc
        url = urlsplit(self.path)
        if url.path == "/shutdown":
            self._send(200, {"ok": True})
            threading.Thread(target=owner.shutdown, daemon=True).start()
            return
        if url.path != "/hook":
            self._send(404)
            return
        length_text = self.headers.get("Content-Length")
        if length_text is None or not length_text.isdigit():
            self.close_connection = True
            self._send(411)
            return
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            owner.log_blindspot("too-large", f"{length} bytes")
            self._drain(limit=4 * MAX_BODY_BYTES)  # authenticated: worth a clean answer
            self.close_connection = True
            self._send(413)
            return
        body = self.rfile.read(length)
        liveness = "liveness=1" in url.query
        status, reply = owner.on_hook(body, liveness, self.headers.get(DOWN_HEADER))
        self._send(status, reply)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # nothing about requests goes to a log by default (G6)


class Server:
    def __init__(
        self,
        ctx: HookContext,
        state: DaemonState,
        *,
        idle_s: float = 1800,
        max_idle_s: float = 86400,
        tick_s: float = 30,
    ) -> None:
        self.ctx = ctx
        self.state = state
        self.idle_s = idle_s
        self.max_idle_s = max_idle_s
        self.tick_s = tick_s
        self.rejected = 0
        self._reported_rejected = 0
        self._lock = threading.Lock()
        self._open: dict[str, bool] = {}  # session id → a turn is open
        self._last_request = ctx.clock.monotonic()
        self._httpd: _HTTPServer | None = None
        self._stopped = threading.Event()
        self.address: tuple[str, int] = ("", 0)

    def touch(self) -> None:
        with self._lock:
            self._last_request = self.ctx.clock.monotonic()

    def count_rejected(self) -> None:
        with self._lock:
            self.rejected += 1

    def log_blindspot(self, reason: str, detail: str) -> None:
        try:
            entry = {"reason": reason, "detail": detail, "event_name": None, "shape": None}
            blindspots.record(self.ctx.io, self.ctx.store.blindspots, entry, self.ctx.clock)
        except Exception:
            pass

    # --- requests -----------------------------------------------------------------------------

    def on_hook(
        self, body: bytes, liveness: bool, down_since: str | None
    ) -> tuple[int, dict[str, Any]]:
        reply, event = handle_event(body, self.ctx)
        self.touch()
        if event is not None:
            self._track(event)
        if liveness:
            reply = self._with_notices(reply, event, down_since)
        return reply.to_http()

    def _track(self, event: Event) -> None:
        with self._lock:
            if event.kind in (Kind.STOP, Kind.SESSION_END):
                self._open[event.session_id] = False
            elif event.kind in (Kind.PROMPT, Kind.TOOL_PRE, Kind.TOOL_POST):
                self._open[event.session_id] = True

    def _with_notices(
        self, reply: HookReply, event: Event | None, down_since: str | None
    ) -> HookReply:
        lines = []
        if down_since and event is not None:
            with contextlib.suppress(Exception):
                self.ctx.recorder().record_gap(
                    event.session_id,
                    "daemon-down",
                    f"agentvcd was not running (it last started at {down_since})",
                    timeout=self.ctx.deadline_s,
                    agent=event.agent,
                )
            lines.append(
                f"AgentVC's background process was not running (it last started at {down_since}); "
                "actions since then may be missing. It has been restarted, and the interval is "
                "listed as a gap."
            )
        lines += self._notices()
        if reply.system_message:
            lines.insert(0, reply.system_message)
        if not lines:
            return reply
        return HookReply(reply.event_name, reply.deny_reason, " ".join(lines), reply.context)

    def _notices(self) -> list[str]:
        io, store = self.ctx.io, self.ctx.store
        path = store.dir / "notices.json"
        try:
            seen = json.loads(io.read_text(path))
        except (FileNotFoundError, ValueError):
            seen = {}
        lines = []
        try:
            gaps = self.ctx.ledger().rows(
                "steps",
                # `agentvc check` makes a gap on purpose; its runs are flagged `check`.
                "where status = 'GAP' and id > ? and run_id not in "
                "(select id from runs where flags like '%\"check\"%')",
                (seen.get("gap_step", 0),),
            )
        except Exception:
            gaps = []
        if gaps:
            n = len(gaps)
            lines.append(
                f"AgentVC: {n} action{'s' if n != 1 else ''} since the last notice "
                f"{'were' if n != 1 else 'was'} not fully recorded (listed as gaps; "
                "see `agentvc inspect`)."
            )
            seen["gap_step"] = max(g["id"] for g in gaps)
        blind = blindspots.count(io, store.blindspots)
        new_blind = blind - int(seen.get("blindspots", 0))
        if new_blind > 0:
            lines.append(
                f"AgentVC: {new_blind} hook payload{'s' if new_blind != 1 else ''} couldn't be "
                "read (.agentvc/blindspots.jsonl)."
            )
        seen["blindspots"] = blind
        with self._lock:
            rejected = self.rejected - self._reported_rejected
            self._reported_rejected = self.rejected
        if rejected:
            lines.append(
                f"AgentVC: {rejected} hook request{'s' if rejected != 1 else ''} "
                f"{'were' if rejected != 1 else 'was'} rejected (wrong token or Host header): "
                "those actions weren't recorded. The hook settings may be stale; "
                "run `agentvc check`."
            )
        for problem in self.ctx.config.problems:
            lines.append(f"AgentVC config: {problem}.")
        with contextlib.suppress(OSError):
            io.write_atomic(path, json.dumps(seen).encode(), fsync=False)
        return lines

    # --- lifecycle ----------------------------------------------------------------------------

    def serve(self) -> None:
        """Serve until shut down or idle. Raises OSError if the port can't be bound."""
        httpd = _HTTPServer(("127.0.0.1", self.state.port), _Handler)
        httpd.agentvc = self
        self._httpd = httpd
        self.address = (str(httpd.server_address[0]), int(httpd.server_address[1]))
        self.state.pid = os.getpid()
        self.state.state = "running"
        self.state.started_at = self.ctx.clock.now().isoformat()
        save_state(self.ctx.io, self.ctx.store, self.state)
        self.touch()
        ticker = threading.Thread(target=self._watch_idle, daemon=True)
        ticker.start()
        try:
            httpd.serve_forever(poll_interval=0.1)
        finally:
            with self._lock:
                still_open = any(self._open.values())
            self.state.state = "stopped-open" if still_open else "stopped"
            self.state.pid = None
            try:
                save_state(self.ctx.io, self.ctx.store, self.state)
            finally:
                httpd.server_close()
                self._stopped.set()

    def _watch_idle(self) -> None:
        while not self._stopped.wait(self.tick_s):
            with self._lock:
                idle = self.ctx.clock.monotonic() - self._last_request
                busy = any(self._open.values())
            if (idle >= self.idle_s and not busy) or idle >= self.max_idle_s:
                self.shutdown()
                return

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()


# --- liveness (the SessionStart / UserPromptSubmit command hook) --------------------------------


def _event_name(body: bytes) -> str | None:
    try:
        name = json.loads(body).get("hook_event_name")
    except (ValueError, AttributeError, RecursionError):
        return None
    return name if isinstance(name, str) else None


def run_liveness(
    body: bytes,
    *,
    workspace: Path,
    io: IO,
    clock: Clock,
    rand: RandomSource,
    environ: Mapping[str, str],
    python: str,
    start_timeout: float = 5.0,
) -> HookReply:
    store = Store(workspace)
    store.ensure(io)
    state = ensure_state(io, store, rand)
    down_since: str | None = None
    problem: str | None = None
    healthy = is_healthy(state)
    if not healthy:
        try:
            # Two sessions starting at once must not both start (and race for) a daemon.
            with io.lock(store.dir / "daemon-start.lock", timeout=start_timeout + 2):
                healthy, down_since, problem = _start(
                    io, store, workspace, python, environ, start_timeout
                )
        except OSError as exc:
            problem = f"it couldn't be started ({exc})"
        state = load_state(io, store) or state
    if healthy:
        headers = {DOWN_HEADER: down_since} if down_since else {}
        try:
            status, data = call(
                state,
                "POST",
                "/hook?liveness=1",
                body,
                headers=headers,
                timeout=wiring.HOOK_TIMEOUT_S,
            )
            if status == 200:
                return _reply_from(data, _event_name(body))
            problem = f"it answered {status}"
        except (OSError, http.client.HTTPException) as exc:
            problem = f"it stopped answering ({exc})"
    return _in_process(body, workspace, io, clock, environ, store, problem or "unknown")


def _start(
    io: IO,
    store: Store,
    workspace: Path,
    python: str,
    environ: Mapping[str, str],
    start_timeout: float,
) -> tuple[bool, str | None, str | None]:
    """Start agentvcd unless another hook just did: (healthy, down since, problem)."""
    state = load_state(io, store)
    assert state is not None
    if is_healthy(state):
        return True, None, None
    down_since = None
    if state.state in ("running", "stopped-open"):
        down_since = state.started_at or "an unknown time"
    if not port_is_free(state.port):
        return False, down_since, f"port {state.port} on 127.0.0.1 is in use by another program"
    io.spawn_detached(
        [python, "-I", "-m", "agentvc", "daemon", "--workspace", str(workspace)],
        cwd=workspace,
        env=dict(environ),
        log=store.daemon_log,
    )
    if wait_healthy(state, start_timeout):
        return True, down_since, None
    return False, down_since, "it didn't answer after starting (see .agentvc/daemon.log)"


def _reply_from(data: dict[str, Any], event_name: str | None) -> HookReply:
    specific = data.get("hookSpecificOutput") or {}
    deny = (
        specific.get("permissionDecisionReason")
        if specific.get("permissionDecision") == "deny"
        else None
    )
    return HookReply(
        event_name,
        deny_reason=deny,
        system_message=data.get("systemMessage"),
        context=specific.get("additionalContext"),
    )


def _in_process(
    body: bytes,
    workspace: Path,
    io: IO,
    clock: Clock,
    environ: Mapping[str, str],
    store: Store,
    problem: str,
) -> HookReply:
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
        event = claude_code.parse_bytes(body, record_prompts=False)
        if isinstance(event, Event):
            with contextlib.suppress(Exception):
                ctx.recorder().record_gap(
                    event.session_id,
                    "daemon-down",
                    f"agentvcd unavailable: {problem}",
                    timeout=ctx.deadline_s,
                    agent=event.agent,
                )
    finally:
        ctx.close()
    message = (
        f"AgentVC's background process is not running: {problem}. Tool calls are NOT being "
        "recorded until it runs (this event was recorded directly). Run `agentvc check`."
    )
    if reply.system_message:
        message = reply.system_message + " " + message
    return HookReply(reply.event_name, reply.deny_reason, message, reply.context)


def run_daemon(
    workspace: Path, environ: Mapping[str, str], io: IO, clock: Clock, rand: RandomSource
) -> int:
    """The `agentvc daemon` entry point: serve this workspace until idle."""
    store = Store(workspace)
    store.ensure(io)
    ctx = HookContext(
        io=io,
        clock=clock,
        workspace=workspace,
        config=load_config(io, store.config),
        anchor_dir=agentvc_home(dict(environ)) / "anchors",
        debug_enabled=enabled_from_env(environ),
    )
    state = ensure_state(io, store, rand)
    try:
        Server(ctx, state).serve()
    except OSError as exc:
        print(f"agentvcd: can't listen on 127.0.0.1:{state.port}: {exc}", flush=True)
        return 1
    finally:
        ctx.close()
    return 0
