# -*- coding: utf-8 -*-
"""Chromium measurements for 20MB/30MB/50MB full chat histories.

Future pagination trigger: keep the full-history GET until repeatable
20MB/30MB/50MB measurements show a loading or retained-memory regression.
Then add an additive archived-history cursor API and a formal AgentScope
history-page callback.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Iterator
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

RUN_LARGE = os.getenv("QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS") == "1"

pytestmark = [
    pytest.mark.ui_smoke,
    pytest.mark.skipif(
        not RUN_LARGE,
        reason="set QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1",
    ),
]

_LATEST_TEXT = "latest-history-message"
_RENDER_ELEMENT_ID = "large-user-39"
_MESSAGE_BUBBLE_SELECTOR = (
    ".qwenpaw-chat-anywhere-message-list "
    ".qwenpaw-bubble-list-scroll > .qwenpaw-bubble[data-role]"
)
_WELCOME_SELECTOR = "[class*='chat-anywhere-message-list-welcome']"


@dataclass(frozen=True)
class BrowserPayload:
    chat_id: str
    history_json: str
    payload_bytes: int


@dataclass(frozen=True)
class BenchmarkApp:
    base_url: str
    chat_requests: list[str]


def build_large_history(target_mb: int) -> BrowserPayload:
    """Build a deterministic full response within 1% of the target size."""
    target_bytes = target_mb * 1024**2
    turn_count = 40
    filler_per_message = (
        target_bytes - 128 * 1024
    ) // (turn_count * 2)
    messages = []
    for turn in range(turn_count):
        messages.extend([
            {
                "id": f"large-user-{turn}",
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": (
                        _LATEST_TEXT
                        if turn == turn_count - 1
                        else f"user-{turn}-"
                    ) + "u" * filler_per_message,
                }],
                "metadata": {
                    "timestamp": "2026-07-30T00:00:00+00:00",
                },
            },
            {
                "id": f"large-assistant-{turn}",
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": f"assistant-{turn}-"
                    + "a" * filler_per_message,
                }],
                "metadata": {
                    "timestamp": "2026-07-30T00:00:01+00:00",
                },
            },
        ])

    body = {"messages": messages, "status": "idle"}

    def encode() -> bytes:
        return json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    last_text = messages[-1]["content"][0]
    correction = target_bytes - len(encode())
    if correction >= 0:
        last_text["text"] += "z" * correction
    else:
        last_text["text"] = last_text["text"][:correction]
    encoded = encode()
    assert abs(len(encoded) - target_bytes) <= target_bytes * 0.01
    return BrowserPayload(
        chat_id=f"large-history-{target_mb}",
        history_json=encoded.decode("utf-8"),
        payload_bytes=len(encoded),
    )


@contextmanager
def serve_benchmark_app(
    payload: BrowserPayload,
) -> Iterator[BenchmarkApp]:
    """Serve the production frontend and payload over real loopback HTTP."""
    dist_dir = Path(__file__).resolve().parents[2] / "console" / "dist"
    if not (dist_dir / "index.html").is_file():
        pytest.fail(
            "console/dist is missing; run `npm run build` before this "
            "opt-in production page benchmark",
        )

    chat_requests: list[str] = []
    large_chat_spec = {
        "id": payload.chat_id,
        "session_id": payload.chat_id,
        "user_id": "admin",
        "channel": "console",
        "name": f"Large {payload.payload_bytes}",
        "created_at": "2026-07-30T00:00:00Z",
        "updated_at": "2026-07-30T00:00:00Z",
        "status": "idle",
        "pinned": False,
    }
    small_chat_spec = {
        **large_chat_spec,
        "id": "small-history",
        "session_id": "small-history",
        "name": "Small history",
    }

    response_bodies = {
        "/api/auth/status": b'{"enabled":false,"has_users":false}',
        "/api/frontend_plugin": b"[]",
        "/api/plugins": b"[]",
        "/api/chats": json.dumps(
            [large_chat_spec, small_chat_spec],
            separators=(",", ":"),
        ).encode("utf-8"),
        f"/api/chats/{payload.chat_id}": payload.history_json.encode(
            "utf-8",
        ),
        "/api/chats/small-history": (
            b'{"messages":[],"status":"idle"}'
        ),
    }

    class Handler(SimpleHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist_dir), **kwargs)

        def log_message(self, format, *args):  # noqa: A002
            return

        def _send_json(self, body: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path.startswith("/api/chats"):
                chat_requests.append(path)
            body = response_bodies.get(path)
            if body is not None:
                self._send_json(body)
                return
            if path.startswith("/api/"):
                self._send_json(b'{"detail":"Not Found"}', status=404)
                return

            candidate = dist_dir / path.lstrip("/")
            if path != "/" and candidate.is_file():
                super().do_GET()
                return
            self.path = "/index.html"
            super().do_GET()

    server = None
    for port in range(18_080, 18_180):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if server is None:
        pytest.fail("no free safe loopback port in 18080-18179")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield BenchmarkApp(
            base_url=f"http://{host}:{port}",
            chat_requests=chat_requests,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def allow_benchmark_server_routes(page: Page, app: BenchmarkApp) -> None:
    """Let selected requests bypass the shared generic API route mock."""
    for path in (
        "/api/auth/status**",
        "/api/frontend_plugin**",
        "/api/plugins**",
        "/api/chats**",
    ):
        page.route(
            f"{app.base_url}{path}",
            lambda route: route.continue_(),
        )


def _performance_metrics(cdp) -> dict[str, float]:
    return {
        item["name"]: item["value"]
        for item in cdp.send("Performance.getMetrics")["metrics"]
    }


def _collect_heap(cdp) -> int:
    cdp.send("HeapProfiler.collectGarbage")
    return int(_performance_metrics(cdp).get("JSHeapUsedSize", 0))


def _spa_navigate(page: Page, path: str) -> None:
    """Navigate React Router without replacing the page's JS context."""
    page.evaluate(
        """path => {
            window.history.pushState({}, "", path);
            window.dispatchEvent(new PopStateEvent("popstate"));
        }""",
        path,
    )
    page.wait_for_url(f"**{path}", timeout=60_000)


def _start_render_measurement(page: Page, marker: str) -> None:
    """Mark the in-page interval ending when the newest bubble is committed."""
    page.evaluate(
        """({ elementId, marker, bubbleSelector }) => {
            window.__compactHistoryObserver?.disconnect();
            performance.clearMarks(`${marker}-start`);
            performance.clearMarks(`${marker}-visible`);
            performance.mark(`${marker}-start`);

            const markVisible = () => {
                if (
                    !document.getElementById(elementId)
                    || document.querySelectorAll(bubbleSelector).length !== 10
                ) {
                    return false;
                }
                performance.mark(`${marker}-visible`);
                window.__compactHistoryObserver?.disconnect();
                return true;
            };
            if (markVisible()) {
                return;
            }
            const observer = new MutationObserver(markVisible);
            window.__compactHistoryObserver = observer;
            observer.observe(document.body, {
                childList: true,
                subtree: true,
            });
        }""",
        {
            "elementId": _RENDER_ELEMENT_ID,
            "marker": marker,
            "bubbleSelector": _MESSAGE_BUBBLE_SELECTOR,
        },
    )


def _wait_for_large_history(page: Page, marker: str) -> float:
    try:
        page.wait_for_function(
            """marker =>
                performance.getEntriesByName(`${marker}-visible`).length > 0
            """,
            arg=marker,
            timeout=30_000,
        )
        page.wait_for_function(
            """selector =>
                document.querySelectorAll(selector).length === 10
            """,
            arg=_MESSAGE_BUBBLE_SELECTOR,
            timeout=60_000,
        )
    except PlaywrightTimeoutError as exc:
        diagnostics = page.evaluate(
            """() => ({
                url: window.location.href,
                currentSessionId: window.currentSessionId || null,
                chatClasses: Array.from(
                    document.querySelectorAll('[class*="chat"]')
                ).slice(0, 30).map((node) => node.className),
                bubbleCount: document.querySelectorAll(
                    '.qwenpaw-chat-anywhere-message-list '
                    + '.qwenpaw-bubble-list-scroll '
                    + '> .qwenpaw-bubble[data-role]'
                ).length,
                renderElementPresent: Boolean(
                    document.getElementById('large-user-39')
                ),
            })""",
        )
        raise AssertionError(
            "large history did not become visible: "
            + json.dumps(diagnostics, ensure_ascii=False),
        ) from exc
    return float(
        page.evaluate(
            """marker => {
                const start = performance.getEntriesByName(
                    `${marker}-start`
                )[0];
                const visible = performance.getEntriesByName(
                    `${marker}-visible`
                )[0];
                return visible.startTime - start.startTime;
            }""",
            marker,
        ),
    )


def _wait_for_small_history(page: Page) -> None:
    page.locator(f"#{_RENDER_ELEMENT_ID}").wait_for(
        state="detached",
        timeout=60_000,
    )
    try:
        page.locator(_WELCOME_SELECTOR).first.wait_for(
            state="visible",
            timeout=15_000,
        )
    except PlaywrightTimeoutError as exc:
        diagnostics = page.evaluate(
            """() => ({
                url: window.location.href,
                title: document.title,
                chatClasses: Array.from(
                    document.querySelectorAll('[class*="chat"]')
                ).slice(0, 30).map((node) => node.className),
                text: (document.body?.innerText || '').slice(0, 1000),
            })""",
        )
        raise AssertionError(
            "small history did not reach the welcome state: "
            + json.dumps(diagnostics, ensure_ascii=False),
        ) from exc


@pytest.mark.parametrize("target_mb", [20, 30, 50])
def test_large_history_page_opens_and_releases_memory(
    mock_api: Page,
    target_mb: int,
):
    """Measure production page open and retained heap across SPA switches."""
    page = mock_api
    payload = build_large_history(target_mb)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    with serve_benchmark_app(payload) as app:
        allow_benchmark_server_routes(page, app)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Performance.enable")
        cdp.send("HeapProfiler.enable")

        page.goto(
            f"{app.base_url}/chat/small-history",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        try:
            _wait_for_small_history(page)
        except AssertionError as exc:
            raise AssertionError(
                f"{exc}; browser errors: {errors}",
            ) from exc
        baseline_heap = _collect_heap(cdp)

        first_marker = f"compact-history-{target_mb}-first"
        _start_render_measurement(page, first_marker)
        _spa_navigate(page, f"/chat/{payload.chat_id}")
        try:
            first_visible_ms = _wait_for_large_history(
                page,
                first_marker,
            )
        except AssertionError as exc:
            raise AssertionError(
                f"{exc}; chat requests: {app.chat_requests}; "
                f"browser errors: {errors}",
            ) from exc
        first_large_heap = _collect_heap(cdp)
        bubble_count = page.locator(_MESSAGE_BUBBLE_SELECTOR).count()

        _spa_navigate(page, "/chat/small-history")
        _wait_for_small_history(page)
        retained_after_first_switch = _collect_heap(cdp)

        second_marker = f"compact-history-{target_mb}-second"
        _start_render_measurement(page, second_marker)
        _spa_navigate(page, f"/chat/{payload.chat_id}")
        second_visible_ms = _wait_for_large_history(page, second_marker)
        second_large_heap = _collect_heap(cdp)
        second_bubble_count = page.locator(
            _MESSAGE_BUBBLE_SELECTOR,
        ).count()

        _spa_navigate(page, "/chat/small-history")
        _wait_for_small_history(page)
        retained_after_second_switch = _collect_heap(cdp)

    assert not errors
    assert first_visible_ms < 5_000
    assert second_visible_ms < 60_000
    assert bubble_count == 10
    assert second_bubble_count == 10
    assert f"/api/chats/{payload.chat_id}" in app.chat_requests
    print(
        "COMPACT_CHAT_HISTORY_PAGE_METRIC "
        + json.dumps({
            "transport": "loopback-http",
            "frontend": "production-build",
            "target_mb": target_mb,
            "payload_bytes": payload.payload_bytes,
            "first_visible_ms": first_visible_ms,
            "second_visible_ms": second_visible_ms,
            "baseline_heap_bytes": baseline_heap,
            "first_large_heap_bytes": first_large_heap,
            "first_extra_heap_bytes": max(
                0,
                first_large_heap - baseline_heap,
            ),
            "retained_after_first_switch_bytes": (
                retained_after_first_switch
            ),
            "first_retained_delta_bytes": max(
                0,
                retained_after_first_switch - baseline_heap,
            ),
            "second_large_heap_bytes": second_large_heap,
            "retained_after_second_switch_bytes": (
                retained_after_second_switch
            ),
            "second_retained_delta_bytes": max(
                0,
                retained_after_second_switch - baseline_heap,
            ),
            "rendered_bubbles": bubble_count,
        }),
    )
