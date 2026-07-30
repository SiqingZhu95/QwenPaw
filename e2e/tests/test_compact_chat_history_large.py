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
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from config.settings import config

RUN_LARGE = os.getenv("QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS") == "1"

pytestmark = [
    pytest.mark.ui_smoke,
    pytest.mark.skipif(
        not RUN_LARGE,
        reason="set QWENPAW_RUN_LARGE_HISTORY_BENCHMARKS=1",
    ),
]

_LATEST_TEXT = "latest-history-message"
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


def register_large_history_routes(
    page: Page,
    payload: BrowserPayload,
) -> list[str]:
    """Override the generic UI-smoke chat mocks with this benchmark data."""
    chat_requests: list[str] = []
    # The shared catch-all ``**/api/**`` mock also matches Vite source module
    # URLs such as ``/src/api/index.ts``. Static modules must bypass all route
    # mocks when this benchmark runs against the development server.
    page.route("**/src/**", lambda route: route.continue_())

    def handle_plugins(route):
        path = urlparse(route.request.url).path.rstrip("/")
        if path in {"/api/plugins", "/api/frontend_plugin"}:
            route.fulfill(
                status=200,
                content_type="application/json",
                body="[]",
            )
        else:
            route.fallback()

    page.route("**/api/plugins**", handle_plugins)
    page.route("**/api/frontend_plugin**", handle_plugins)

    def handle_auth_status(route):
        path = urlparse(route.request.url).path.rstrip("/")
        if path == "/api/auth/status":
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"enabled":false,"has_users":false}',
            )
        else:
            route.fallback()

    page.route("**/api/auth/status**", handle_auth_status)

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

    def handle_chats(route):
        path = urlparse(route.request.url).path.rstrip("/")
        if path.startswith("/api/chats"):
            chat_requests.append(path)
        if path == "/api/chats":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps([large_chat_spec, small_chat_spec]),
            )
        elif path == f"/api/chats/{payload.chat_id}":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=payload.history_json,
            )
        elif path == "/api/chats/small-history":
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"messages":[],"status":"idle"}',
            )
        else:
            route.fallback()

    # Playwright resolves routes last-registered first, so this overrides
    # mock_api's generic /api/chats handlers without changing shared fixtures.
    page.route("**/api/chats**", handle_chats)
    return chat_requests


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


def _wait_for_large_history(page: Page) -> None:
    try:
        page.get_by_text(_LATEST_TEXT, exact=False).first.wait_for(
            state="visible",
            timeout=30_000,
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
                text: (document.body?.innerText || '').slice(0, 1000),
            })""",
        )
        raise AssertionError(
            "large history did not become visible: "
            + json.dumps(diagnostics, ensure_ascii=False),
        ) from exc
    page.locator(_MESSAGE_BUBBLE_SELECTOR).first.wait_for(
        state="visible",
        timeout=60_000,
    )


def _wait_for_small_history(page: Page) -> None:
    page.get_by_text(_LATEST_TEXT, exact=False).wait_for(
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
    """Measure first/cached open and retained heap across SPA switches."""
    page = mock_api
    payload = build_large_history(target_mb)
    chat_requests = register_large_history_routes(page, payload)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cdp = page.context.new_cdp_session(page)
    cdp.send("Performance.enable")
    cdp.send("HeapProfiler.enable")

    page.goto(
        f"{config.server.base_url}/chat/small-history",
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

    started = time.perf_counter()
    _spa_navigate(page, f"/chat/{payload.chat_id}")
    try:
        _wait_for_large_history(page)
    except AssertionError as exc:
        raise AssertionError(
            f"{exc}; chat requests: {chat_requests}; "
            f"browser errors: {errors}",
        ) from exc
    first_visible_ms = (time.perf_counter() - started) * 1000
    first_large_heap = _collect_heap(cdp)
    bubble_count = page.locator(_MESSAGE_BUBBLE_SELECTOR).count()

    _spa_navigate(page, "/chat/small-history")
    _wait_for_small_history(page)
    retained_after_first_switch = _collect_heap(cdp)

    second_started = time.perf_counter()
    _spa_navigate(page, f"/chat/{payload.chat_id}")
    _wait_for_large_history(page)
    second_visible_ms = (time.perf_counter() - second_started) * 1000
    second_large_heap = _collect_heap(cdp)
    second_bubble_count = page.locator(_MESSAGE_BUBBLE_SELECTOR).count()

    _spa_navigate(page, "/chat/small-history")
    _wait_for_small_history(page)
    retained_after_second_switch = _collect_heap(cdp)

    assert not errors
    assert first_visible_ms < 60_000
    assert second_visible_ms < 60_000
    assert bubble_count == 10
    assert second_bubble_count == 10
    print(
        "COMPACT_CHAT_HISTORY_PAGE_METRIC "
        + json.dumps({
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
