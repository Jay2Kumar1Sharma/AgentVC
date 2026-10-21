"""Spike S1/D1 (plan §7.1): how long does one hook call cost when each call is a new process?

Measures, end to end from the caller's side (as the agent sees it):

- python_noop             a bare interpreter start (`python -c pass`)
- hook_minimal            a handler that reads a JSON payload from stdin and exits
- hook_minimal_isolated   the same with `-I -S` (no site-packages; AgentVC is stdlib-only)
- hook_via_bash           the minimal handler launched through bash, as Claude Code does on
                          Windows (skipped when bash is not on PATH)
- snapshot_first          per repo size: the first snapshot of a generated test repo
- snapshot_unchanged      per repo size: a snapshot when nothing changed since the last one
- snapshot_one_file_changed  per repo size: a snapshot after one file changed

and, for unknown U-18, the round trip of an `http` hook (Claude Code POSTs the payload as JSON to
a URL) to a local server on 127.0.0.1 that checks a token header, parses the payload and answers
`{}`. The client runs inside an already-running process, as the agent's does:

- http_hook_new_connection  a new TCP connection per call (a client without keep-alive)
- http_hook_keepalive       one persistent connection for every call
- http_hook_localhost       a new connection to the name `localhost` instead of 127.0.0.1
                            (at most 20 runs)
- http_hook_large_payload   new connection, a Write payload carrying a 256 KB file
- http_hook_node_fetch      Node's built-in `fetch`, the closest stand-in for the agent's own
                            JavaScript client (skipped when node is not on PATH)

A snapshot is what AgentVC's snapshotter would do (plan §7): `git add -A` into a private index
and object store outside the work tree, then `git write-tree`, run inside the handler process.

Usage:
    python scripts/spikes/hook_latency.py --sizes 1000 10000 100000 --runs 50 \\
        --out results/spikes/2026-10-18
    python scripts/spikes/hook_latency.py --http-only --runs 200 --out results/spikes/2026-10-21
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import math
import os
import platform
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any

PAYLOAD = json.dumps(
    {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "transcript_path": "~/.claude/projects/x/s.jsonl",
        "cwd": "~/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello", "description": "Print hello"},
        "tool_use_id": "toolu_0000000000000000000000",
    }
)

LARGE_PAYLOAD = json.dumps(
    {
        "session_id": "00000000-0000-0000-0000-000000000000",
        "transcript_path": "~/.claude/projects/x/s.jsonl",
        "cwd": "~/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "~/project/big.txt", "content": "x" * 256 * 1024},
        "tool_use_id": "toolu_0000000000000000000001",
    }
)

MINIMAL_HANDLER = """\
import json, sys
json.loads(sys.stdin.read())
"""

SNAPSHOT_HANDLER = """\
import json, os, subprocess, sys
json.loads(sys.stdin.read())
repo, store, index = sys.argv[1:4]
env = dict(os.environ, GIT_INDEX_FILE=index)
git = ["git", "-c", "core.autocrlf=false", "--git-dir", store, "--work-tree", repo]
subprocess.run(git + ["add", "-A"], env=env, check=True, capture_output=True)
subprocess.run(git + ["write-tree"], env=env, check=True, capture_output=True)
"""

TOKEN_HEADER = "X-AgentVC-Token"

NODE_CLIENT = """\
const [url, token, payloadFile, runs] = process.argv.slice(2);
const body = require("fs").readFileSync(payloadFile, "utf8");
const headers = {"Content-Type": "application/json", "X-AgentVC-Token": token};
(async () => {
  const samples = [];
  for (let i = 0; i <= Number(runs); i++) {
    const start = performance.now();
    const res = await fetch(url, {method: "POST", headers, body});
    await res.text();
    if (res.status !== 200) throw new Error("status " + res.status);
    if (i > 0) samples.push(performance.now() - start);  // the first call is the warm-up
  }
  console.log(JSON.stringify(samples));
})();
"""


def _stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 1),
        "max_ms": round(ordered[-1], 1),
        "mean_ms": round(statistics.fmean(ordered), 1),
    }


def _time_ms(command: list[str], before: Callable[[], None] | None = None) -> float:
    if before:
        before()
    start = time.perf_counter()
    subprocess.run(command, input=PAYLOAD, text=True, check=True, capture_output=True, timeout=900)
    return (time.perf_counter() - start) * 1000


def _measure(
    name: str, command: list[str], runs: int, before: Callable[[], None] | None = None, **extra: Any
) -> dict[str, Any]:
    _time_ms(command, before)  # warm-up: file-system cache, antivirus first scan
    samples = [round(_time_ms(command, before), 2) for _ in range(runs)]
    result = {"name": name, "runs": runs, **extra, **_stats(samples), "samples_ms": samples}
    print(
        f"  {name:<28}{extra.get('files', ''):>8}  p50 {result['p50_ms']:>8} ms"
        f"  p95 {result['p95_ms']:>8} ms"
    )
    return result


class _HookHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # allows keep-alive
    server: _HookHTTPServer

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.headers.get(TOKEN_HEADER) != self.server.token:
            self.server.count(accepted=False)
            self._answer(401, b"")
            return
        json.loads(body)
        self.server.count(accepted=True)
        self._answer(200, b"{}")

    def _answer(self, status: int, body: bytes) -> None:
        # Status line, headers and body in one write: separate small writes can stall on
        # Nagle's algorithm meeting delayed ACKs.
        head = (
            f"HTTP/1.1 {status} {self.responses[status][0]}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
        )
        self.wfile.write(head.encode("ascii") + body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _HookHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token: str) -> None:
        super().__init__(("127.0.0.1", 0), _HookHandler)
        self.token = token
        self.accepted = 0
        self.rejected = 0
        self._lock = threading.Lock()

    def count(self, accepted: bool) -> None:
        with self._lock:
            if accepted:
                self.accepted += 1
            else:
                self.rejected += 1


class HookServer:
    """A stand-in for `agentvcd`: 127.0.0.1 only, a random port, a token per session."""

    def __init__(self) -> None:
        self.token = secrets.token_hex(16)
        self._server = _HookHTTPServer(self.token)
        self.port: int = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def accepted(self) -> int:
        return self._server.accepted

    @property
    def rejected(self) -> int:
        return self._server.rejected

    def __enter__(self) -> HookServer:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _post(conn: http.client.HTTPConnection, token: str, body: bytes) -> None:
    headers = {"Content-Type": "application/json", TOKEN_HEADER: token}
    conn.request("POST", "/hook", body=body, headers=headers)
    response = conn.getresponse()
    response.read()
    if response.status != 200:
        raise RuntimeError(f"hook server answered {response.status}")


def _timed_calls(call: Callable[[], None], runs: int) -> list[float]:
    call()  # warm-up
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        call()
        samples.append(round((time.perf_counter() - start) * 1000, 3))
    return samples


def _http_result(name: str, samples: list[float], payload: str) -> dict[str, Any]:
    result = {
        "name": name,
        "runs": len(samples),
        "payload_bytes": len(payload.encode("utf-8")),
        **_stats(samples),
        "samples_ms": samples,
    }
    print(f"  {name:<28}{'':>8}  p50 {result['p50_ms']:>8} ms  p95 {result['p95_ms']:>8} ms")
    return result


def run_http(
    runs: int, workdir: Path, server: HookServer, node: str | None
) -> list[dict[str, Any]]:
    """Time ``runs`` hook POSTs per variant against a running ``server``."""
    print("HTTP hook round trip (U-18):")

    def new_connection(host: str, payload: str) -> Callable[[], None]:
        body = payload.encode("utf-8")

        def call() -> None:
            conn = http.client.HTTPConnection(host, server.port, timeout=30)
            try:
                _post(conn, server.token, body)
            finally:
                conn.close()

        return call

    results = [
        _http_result(
            "http_hook_new_connection",
            _timed_calls(new_connection("127.0.0.1", PAYLOAD), runs),
            PAYLOAD,
        )
    ]
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
    try:
        body = PAYLOAD.encode("utf-8")
        samples = _timed_calls(lambda: _post(conn, server.token, body), runs)
        results.append(_http_result("http_hook_keepalive", samples, PAYLOAD))
    finally:
        conn.close()
    # Capped: on Windows each call can cost seconds (the name resolves to ::1 first, where
    # nothing listens, before 127.0.0.1 is tried).
    localhost_runs = min(runs, 20)
    results.append(
        _http_result(
            "http_hook_localhost",
            _timed_calls(new_connection("localhost", PAYLOAD), localhost_runs),
            PAYLOAD,
        )
    )
    results.append(
        _http_result(
            "http_hook_large_payload",
            _timed_calls(new_connection("127.0.0.1", LARGE_PAYLOAD), runs),
            LARGE_PAYLOAD,
        )
    )
    if node:
        workdir.mkdir(parents=True, exist_ok=True)
        client = workdir / "node_client.js"
        client.write_text(NODE_CLIENT, encoding="utf-8")
        payload_file = workdir / "payload.json"
        payload_file.write_text(PAYLOAD, encoding="utf-8")
        url = f"http://127.0.0.1:{server.port}/hook"
        done = subprocess.run(
            [node, str(client), url, server.token, str(payload_file), str(runs)],
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        samples = [round(float(v), 3) for v in json.loads(done.stdout)]
        results.append(_http_result("http_hook_node_fetch", samples, PAYLOAD))
    return results


def _run_http_measurements(runs: int, workdir: Path) -> list[dict[str, Any]]:
    with HookServer() as server:
        return run_http(runs, workdir, server=server, node=shutil.which("node"))


def make_repo(root: Path, files: int) -> None:
    """Generate ``files`` small text files, 100 per folder, with deterministic content."""
    for i in range(files):
        folder = root / f"d{i // 100:04d}"
        if i % 100 == 0:
            folder.mkdir(parents=True, exist_ok=True)
        (folder / f"f{i:06d}.txt").write_text(f"file {i}\n" + "x" * 200 + "\n", encoding="utf-8")


def _file_changer(target: Path) -> Callable[[], None]:
    """Return a function that gives ``target`` new content on every call."""
    counter = [0]

    def change() -> None:
        counter[0] += 1
        target.write_text(f"changed {counter[0]}\n", encoding="utf-8")

    return change


def _environment() -> dict[str, Any]:
    git = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True)
    node = shutil.which("node")
    node_version = None
    if node:
        done = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
        node_version = done.stdout.strip() or None
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "git": git.stdout.strip(),
        "bash_on_path": shutil.which("bash") is not None,
        "node": node_version,
    }


def run(sizes: list[int], runs: int, workdir: Path) -> dict[str, Any]:
    py = sys.executable
    workdir.mkdir(parents=True, exist_ok=True)
    minimal = workdir / "minimal_handler.py"
    minimal.write_text(MINIMAL_HANDLER, encoding="utf-8")
    snapshot = workdir / "snapshot_handler.py"
    snapshot.write_text(SNAPSHOT_HANDLER, encoding="utf-8")

    print("Process start:")
    results = [
        _measure("python_noop", [py, "-c", "pass"], runs),
        _measure("hook_minimal", [py, str(minimal)], runs),
        _measure("hook_minimal_isolated", [py, "-I", "-S", str(minimal)], runs),
    ]
    bash = shutil.which("bash")
    if bash:
        line = f'"{Path(py).as_posix()}" "{minimal.as_posix()}"'
        results.append(_measure("hook_via_bash", [bash, "-c", line], runs))
    results.extend(_run_http_measurements(runs, workdir))

    for files in sizes:
        print(f"Snapshot, {files} files (generating repo...):")
        repo, store, index = (
            workdir / f"repo-{files}",
            workdir / f"store-{files}",
            workdir / f"index-{files}",
        )
        for path in (repo, store):
            shutil.rmtree(path, ignore_errors=True)
        index.unlink(missing_ok=True)
        make_repo(repo, files)
        subprocess.run(["git", "--git-dir", str(store), "init", "-q"], check=True)
        command = [py, "-I", "-S", str(snapshot), str(repo), str(store), str(index)]

        start = time.perf_counter()
        subprocess.run(
            command, input=PAYLOAD, text=True, check=True, capture_output=True, timeout=3600
        )
        first = round((time.perf_counter() - start) * 1000, 2)
        results.append(
            {
                "name": "snapshot_first",
                "files": files,
                "runs": 1,
                **_stats([first]),
                "samples_ms": [first],
            }
        )
        print(f"  {'snapshot_first':<28}{files:>8}  {first:>8} ms")

        size_runs = max(3, runs // 5) if files >= 100_000 else runs
        results.append(_measure("snapshot_unchanged", command, size_runs, files=files))
        touch_one = _file_changer(repo / "d0000" / "f000000.txt")
        results.append(
            _measure("snapshot_one_file_changed", command, size_runs, before=touch_one, files=files)
        )
    return {"environment": _environment(), "measurements": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10_000, 100_000])
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--out", type=Path, required=True, help="folder for the JSON result")
    parser.add_argument("--workdir", type=Path, help="scratch folder (default: a temp folder)")
    parser.add_argument(
        "--http-only", action="store_true", help="only the http hook round trip (U-18)"
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="agentvc-spike-") as tmp:
        workdir = args.workdir or Path(tmp)
        if args.http_only:
            result = {
                "environment": _environment(),
                "measurements": _run_http_measurements(args.runs, workdir),
            }
        else:
            result = run(args.sizes, args.runs, workdir)
    result["meta"] = {
        "script": "scripts/spikes/hook_latency.py",
        "date": date.today().isoformat(),
        "sizes": [] if args.http_only else args.sizes,
        "runs": args.runs,
        "http_only": args.http_only,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    kind = "hook_latency_http" if args.http_only else "hook_latency"
    name = f"{kind}-{platform.system().lower()}.json"
    (args.out / name).write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Wrote {args.out / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
