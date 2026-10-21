"""agentvcd (ADR-0003, design note hook-path.md): localhost-only server, token, liveness per prompt.

FM-HOOK-04 (daemon down while hooks are http), FM-HOOK-05 (port taken), FM-HOOK-06 (rejected
requests), FM-HOOK-07 (never `localhost`), U-02 (concurrent hooks).
"""

import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agentvc import daemon, hook
from agentvc.clock import SeededRandom, SystemClock, SystemRandom
from agentvc.config import Config
from agentvc.io import IO
from agentvc.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "hosts" / "claude-code" / "2.1.302"


def body(ws: Path, name: str, **changes: Any) -> bytes:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["payload"]
    payload.update(cwd=str(ws), **changes)
    return json.dumps(payload).encode()


def make_ctx(ws: Path) -> hook.HookContext:
    return hook.HookContext(
        io=IO(),
        clock=SystemClock(),
        workspace=ws,
        config=Config(),
        anchor_dir=ws.parent / "home" / "anchors",
    )


def request(
    state: daemon.DaemonState,
    method: str,
    path: str,
    data: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", state.port, timeout=10)
    try:
        conn.request(method, path, body=data, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}
    finally:
        conn.close()


def auth(state: daemon.DaemonState) -> dict[str, str]:
    return {"Content-Type": "application/json", daemon.TOKEN_HEADER: state.token}


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[daemon.Server, daemon.DaemonState, hook.HookContext]]:
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    ctx.store.ensure(ctx.io)
    state = daemon.ensure_state(ctx.io, ctx.store, SeededRandom(1))
    server = daemon.Server(ctx, state)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    assert daemon.wait_healthy(state, timeout=5)
    yield server, state, ctx
    server.shutdown()
    thread.join(timeout=10)
    ctx.close()


def test_state_keeps_port_and_token_across_restarts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.ensure(IO())
    first = daemon.ensure_state(IO(), store, SystemRandom())
    again = daemon.ensure_state(IO(), store, SystemRandom())
    assert (first.port, first.token) == (again.port, again.token)
    assert 1024 < first.port < 65536 and len(first.token) == 32


def test_port_is_outside_every_ephemeral_range(tmp_path: Path) -> None:
    # A client whose ephemeral source port equals the target port connects to itself when
    # nothing listens there (seen on Windows): the daemon's port must never be ephemeral.
    for i in range(20):
        store = Store(tmp_path / f"w{i}")
        store.ensure(IO())
        port = daemon.ensure_state(IO(), store, SeededRandom(i)).port
        assert 20000 <= port < 32768


def test_a_garbage_answer_means_not_healthy(tmp_path: Path) -> None:
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen()

    def serve_garbage() -> None:
        conn, _ = echo.accept()
        conn.recv(1000)
        conn.sendall(b"GET /health HTTP/1.1\r\n\r\n")  # what a self-connection hands back
        conn.close()

    threading.Thread(target=serve_garbage, daemon=True).start()
    state = daemon.DaemonState(port=echo.getsockname()[1], token="t")
    try:
        assert daemon.is_healthy(state) is False
    finally:
        echo.close()


def test_hook_events_are_recorded_over_http(served: Any) -> None:
    _server, state, ctx = served
    ws = ctx.workspace
    for name in (
        "0003-UserPromptSubmit.json",
        "0004-PreToolUse-Bash.json",
        "0005-PostToolUse-Bash.json",
    ):
        status, reply = request(state, "POST", "/hook", body(ws, name), auth(state))
        assert (status, reply) == (200, {})
    [step] = ctx.ledger().rows("steps")
    assert step["status"] == "RECORDED"


def test_requests_without_the_token_are_rejected_and_counted(served: Any) -> None:
    server, state, ctx = served
    data = body(ctx.workspace, "0004-PreToolUse-Bash.json")
    assert request(state, "POST", "/hook", data, {"Content-Type": "application/json"})[0] == 401
    wrong = {**auth(state), daemon.TOKEN_HEADER: "0" * 32}
    assert request(state, "POST", "/hook", data, wrong)[0] == 401
    assert server.rejected == 2
    assert ctx.ledger().rows("steps") == []


def test_a_foreign_host_header_is_rejected(served: Any) -> None:
    # DNS rebinding: a web page reaching 127.0.0.1 under another name.
    _server, state, ctx = served
    headers = {**auth(state), "Host": "evil.example:80"}
    data = body(ctx.workspace, "0004-PreToolUse-Bash.json")
    assert request(state, "POST", "/hook", data, headers)[0] == 403


def test_the_server_listens_on_loopback_only(served: Any) -> None:
    server, _state, _ctx = served
    assert server.address[0] == "127.0.0.1"


@pytest.mark.parametrize("reuse", [False, True])
def test_no_other_socket_can_take_over_the_daemons_port(served: Any, reuse: bool) -> None:
    # On Windows a listener with SO_REUSEADDR lets another socket bind the same port and
    # intercept connections (and with them the token).
    _server, state, _ctx = served
    thief = socket.socket()
    if reuse:
        thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            thief.bind(("127.0.0.1", state.port))
    finally:
        thief.close()
    assert daemon.is_healthy(state)


def test_oversized_bodies_are_refused_and_logged(
    served: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _server, state, ctx = served
    monkeypatch.setattr(daemon, "MAX_BODY_BYTES", 1000)
    data = body(
        ctx.workspace,
        "0038-PreToolUse-Write.json",
        tool_input={"file_path": "x", "content": "y" * 5000},
    )
    assert request(state, "POST", "/hook", data, auth(state))[0] == 413
    lines = ctx.store.blindspots.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["reason"] == "too-large"


def test_parallel_hooks_lose_nothing(served: Any) -> None:
    _server, state, ctx = served
    ws = ctx.workspace
    request(state, "POST", "/hook", body(ws, "0003-UserPromptSubmit.json"), auth(state))

    def call(i: int) -> None:
        for name in ("0004-PreToolUse-Bash.json", "0005-PostToolUse-Bash.json"):
            request(
                state, "POST", "/hook", body(ws, name, tool_use_id=f"toolu_{i:04d}"), auth(state)
            )

    threads = [threading.Thread(target=call, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = ctx.ledger().rows("steps")
    assert len(rows) == 12 and {r["status"] for r in rows} == {"RECORDED"}
    assert ctx.ledger().verify().status == "ok"


def test_liveness_reply_reports_gaps_blind_spots_and_rejections(served: Any) -> None:
    _server, state, ctx = served
    ws = ctx.workspace
    request(state, "POST", "/hook", body(ws, "0003-UserPromptSubmit.json"), auth(state))
    request(state, "POST", "/hook", body(ws, "0005-PostToolUse-Bash.json"), auth(state))  # no Pre
    request(state, "POST", "/hook", b"{broken", auth(state))
    request(state, "POST", "/hook", b"{}", {"Content-Type": "application/json"})  # no token
    _, reply = request(
        state, "POST", "/hook?liveness=1", body(ws, "0003-UserPromptSubmit.json"), auth(state)
    )
    message = reply["systemMessage"]
    assert "1 action" in message and "gap" in message
    assert "1 hook payload" in message
    assert "1 hook request" in message and "rejected" in message
    _status, reply = request(
        state, "POST", "/hook?liveness=1", body(ws, "0003-UserPromptSubmit.json"), auth(state)
    )
    assert "systemMessage" not in reply  # each problem is reported once


def test_idle_daemon_exits_only_when_no_turn_is_open(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = make_ctx(ws)
    ctx.store.ensure(ctx.io)
    state = daemon.ensure_state(ctx.io, ctx.store, SystemRandom())
    server = daemon.Server(ctx, state, idle_s=60, tick_s=0.05)
    thread = threading.Thread(target=server.serve, daemon=True)
    thread.start()
    assert daemon.wait_healthy(state, timeout=5)
    request(state, "POST", "/hook", body(ws, "0003-UserPromptSubmit.json"), auth(state))
    server.idle_s = 0.3  # only now: start-up itself must not race the idle limit
    time.sleep(0.8)
    assert thread.is_alive()  # a turn is open: tool calls may still come
    request(state, "POST", "/hook", body(ws, "0008-Stop.json"), auth(state))
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert daemon.load_state(ctx.io, ctx.store).state == "stopped"  # type: ignore[union-attr]
    ctx.close()


# --- liveness with real processes ----------------------------------------------------------


def env_for(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "AGENTVC_HOME": str(tmp_path / "home")}


def liveness(ws: Path, data: bytes, env: dict[str, str]) -> hook.HookReply:
    return daemon.run_liveness(
        data,
        workspace=ws,
        io=IO(),
        clock=SystemClock(),
        rand=SystemRandom(),
        environ=env,
        python=sys.executable,
    )


def stop(ws: Path) -> None:
    state = daemon.load_state(IO(), Store(ws))
    if state is not None:
        with contextlib.suppress(OSError):
            request(state, "POST", "/shutdown", b"", auth(state))


@pytest.mark.slow
def test_liveness_starts_the_daemon_and_recovers_from_a_kill(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    env = env_for(tmp_path)
    try:
        reply = liveness(ws, body(ws, "0002-SessionStart.json"), env)
        assert reply.system_message is None, reply
        state = daemon.load_state(IO(), Store(ws))
        assert state is not None and state.state == "running" and state.pid
        first_pid = state.pid
        liveness(ws, body(ws, "0003-UserPromptSubmit.json"), env)
        assert daemon.load_state(IO(), Store(ws)).pid == first_pid  # type: ignore[union-attr]

        subprocess.run(  # the agent (or a crash) kills agentvcd
            ["taskkill", "/F", "/PID", str(first_pid)]
            if sys.platform == "win32"
            else ["kill", "-9", str(first_pid)],
            capture_output=True,
            check=True,
        )
        time.sleep(0.5)
        reply = liveness(ws, body(ws, "0003-UserPromptSubmit.json"), env)
        assert reply.system_message and "not running" in reply.system_message
        restarted = daemon.load_state(IO(), Store(ws))
        assert restarted is not None and restarted.pid != first_pid
        assert (restarted.port, restarted.token) == (state.port, state.token)
    finally:
        stop(ws)
    ctx = make_ctx(ws)
    ctx.anchor_dir = tmp_path / "home" / "anchors"
    gaps = [s for s in ctx.ledger().rows("steps") if s["gap_reason"] == "daemon-down"]
    assert len(gaps) == 1
    ctx.close()


@pytest.mark.slow
def test_a_taken_port_falls_back_to_recording_in_process(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    env = env_for(tmp_path)
    store = Store(ws)
    store.ensure(IO())
    state = daemon.ensure_state(IO(), store, SystemRandom())
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", state.port))
    blocker.listen()
    try:
        reply = liveness(ws, body(ws, "0003-UserPromptSubmit.json"), env)
    finally:
        blocker.close()
        stop(ws)
    assert reply.system_message and str(state.port) in reply.system_message
    ctx = make_ctx(ws)
    ctx.anchor_dir = tmp_path / "home" / "anchors"
    assert len(ctx.ledger().rows("turns")) == 1  # the event itself was still recorded
    ctx.close()
