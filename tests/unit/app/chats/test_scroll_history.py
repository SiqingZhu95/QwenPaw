# -*- coding: utf-8 -*-
"""Tests for restoring Scroll-archived messages for chat display."""
from __future__ import annotations

from qwenpaw.app.chats.scroll_history import extract_index_ranges


def test_extract_index_ranges_merges_tiers_and_adjacent_spans():
    """A carried index must recover every archived seq exactly once."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "tiers": [
                [
                    {"seq_lo": 20, "seq_hi": 29, "lines": []},
                    {"seq_lo": 30, "seq_hi": 35, "lines": []},
                ],
                [{"seq_lo": 1, "seq_hi": 19, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(
        scroll,
        session_id="session-1",
    ) == [(1, 35)]


def test_extract_index_ranges_accepts_legacy_levels():
    """Older checkpoints used ``levels`` and must remain readable."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "levels": [
                [{"seq_lo": 5, "seq_hi": 8, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(
        scroll,
        session_id="session-1",
    ) == [(5, 8)]


def test_extract_index_ranges_rejects_other_session():
    """A copied or corrupt checkpoint must not leak another session."""
    scroll = {
        "index": {
            "session_id": "other-session",
            "tiers": [
                [{"seq_lo": 1, "seq_hi": 10, "lines": []}],
            ],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == []


def test_extract_index_ranges_ignores_invalid_values():
    """Booleans, strings, and reversed spans are not valid seq ranges."""
    scroll = {
        "index": {
            "session_id": "session-1",
            "tiers": [
                [
                    {"seq_lo": True, "seq_hi": 3, "lines": []},
                    {"seq_lo": 9, "seq_hi": 4, "lines": []},
                    {"seq_lo": "1", "seq_hi": 2, "lines": []},
                ],
            ],
        },
    }

    assert extract_index_ranges(scroll, session_id="session-1") == []
