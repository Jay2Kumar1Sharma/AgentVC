"""Render check for architecture/index.html (plan §5.3): the page must actually draw.

Serves architecture/ on 127.0.0.1 and drives a headless Chromium with Playwright. Fails (never
skips) when Playwright or its browser is missing: run `python -m playwright install chromium`.
"""

import functools
import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, sync_playwright

ARCH = Path(__file__).resolve().parent.parent / "architecture"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def served() -> Iterator[str]:
    handler = functools.partial(QuietHandler, directory=str(ARCH))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def open_page(page: Page, url: str, problems: list[str], requests: list[str]) -> None:
    page.on("pageerror", lambda error: problems.append(f"page error: {error}"))
    page.on(
        "console",
        lambda msg: (
            problems.append(f"console {msg.type}: {msg.text}") if msg.type == "error" else None
        ),
    )
    page.on("request", lambda request: requests.append(request.url))
    page.goto(url)
    page.wait_for_selector('body[data-rendered="true"]', timeout=10_000)


@pytest.mark.slow
def test_overview_renders_and_works(served: str) -> None:
    graph = json.loads((ARCH / "graph.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    requests: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            open_page(page, served, problems, requests)

            assert page.locator(".node").count() == len(graph["nodes"])
            assert page.locator(".layer").count() == len(graph["layers"])
            assert page.locator("#edges line").count() == len(graph["edges"])

            first = graph["nodes"][0]
            page.locator(f'[data-node-id="{first["id"]}"]').click()
            assert page.locator("#details h3").inner_text() == first["label"]

            page.set_viewport_size({"width": 375, "height": 800})
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 0, f"horizontal scroll at phone width: {overflow}px"
        finally:
            browser.close()

    assert problems == []
    external = [u for u in requests if urlparse(u).hostname != "127.0.0.1"]
    assert external == [], f"the page requested external resources: {external}"
