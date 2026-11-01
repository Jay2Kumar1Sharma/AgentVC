"""Smoke test for scripts/spikes/hook_latency.py (spike S1/D1) with tiny sizes."""

import json
from pathlib import Path

import pytest

from scripts.spikes import hook_latency


@pytest.mark.slow
def test_spike_writes_every_measurement(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = hook_latency.main(
        ["--sizes", "30", "--runs", "3", "--out", str(out), "--workdir", str(tmp_path / "work")]
    )
    assert code == 0
    [result_file] = out.glob("hook_latency-*.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert {"os", "python", "git"} <= set(result["environment"])
    names = {m["name"] for m in result["measurements"]}
    assert {"python_noop", "hook_minimal", "hook_minimal_isolated", "snapshot_first"} <= names
    assert {"snapshot_unchanged", "snapshot_one_file_changed"} <= names
    assert {"http_hook_new_connection", "http_hook_keepalive"} <= names
    for m in result["measurements"]:
        assert len(m["samples_ms"]) == m["runs"] > 0
        assert 0 < m["p50_ms"] <= m["p95_ms"] <= m["max_ms"]
    snapshot = [m for m in result["measurements"] if m["name"] == "snapshot_one_file_changed"]
    assert snapshot[0]["files"] == 30


# U-18: the round trip of a Claude Code-style `http` hook to a local server (ADR-0003).

HTTP_NAMES = {
    "http_hook_new_connection",
    "http_hook_keepalive",
    "http_hook_localhost",
    "http_hook_large_payload",
}


@pytest.mark.slow
def test_http_measurements_reach_the_authenticated_server(tmp_path: Path) -> None:
    server = hook_latency.HookServer()
    with server:
        results = hook_latency.run_http(3, tmp_path, server=server, node=None)
    assert {m["name"] for m in results} == HTTP_NAMES
    for m in results:
        assert len(m["samples_ms"]) == m["runs"] == 3
        assert 0 < m["p50_ms"] <= m["p95_ms"] <= m["max_ms"]
    # every request (3 runs + 1 warm-up per measurement) arrived, parsed, with the token
    assert server.accepted == 4 * len(HTTP_NAMES)
    assert server.rejected == 0
    large = [m for m in results if m["name"] == "http_hook_large_payload"]
    assert large[0]["payload_bytes"] > 200_000


def test_http_server_rejects_requests_without_the_token() -> None:
    import http.client

    server = hook_latency.HookServer()
    with server:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("POST", "/hook", body=b"{}", headers={"Content-Type": "application/json"})
        status = conn.getresponse().status
        conn.close()
    assert status == 401
    assert server.rejected == 1
    assert server.accepted == 0


@pytest.mark.slow
def test_http_measurement_through_node_fetch(tmp_path: Path) -> None:
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    server = hook_latency.HookServer()
    with server:
        results = hook_latency.run_http(3, tmp_path, server=server, node=node)
    [fetch] = [m for m in results if m["name"] == "http_hook_node_fetch"]
    assert len(fetch["samples_ms"]) == 3
    assert server.accepted == 4 * (len(HTTP_NAMES) + 1)


@pytest.mark.slow
def test_http_only_run_writes_its_own_file(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = hook_latency.main(["--http-only", "--runs", "3", "--out", str(out)])
    assert code == 0
    [result_file] = out.glob("hook_latency_http-*.json")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert {m["name"] for m in result["measurements"]} >= HTTP_NAMES
    assert result["meta"]["http_only"] is True
