# -*- coding: utf-8 -*-
"""Adapters for converting Runtime events into JSON-safe dictionaries."""
from __future__ import annotations

import json
from typing import Any, Mapping


class SubagentStreamSerializationError(ValueError):
    """Raised when a Runtime item cannot be represented safely as JSON."""


class RuntimeEventSerializer:
    """Serialize without mutating or stringifying arbitrary Runtime objects."""

    def serialize(self, event: Any) -> dict[str, Any]:
        if isinstance(event, Mapping):
            payload = dict(event)
        elif hasattr(event, "model_dump"):
            payload = event.model_dump(mode="json")
        elif hasattr(event, "dict"):
            payload = event.dict()
        else:
            raise SubagentStreamSerializationError(
                f"Unsupported Runtime event type: {type(event).__name__}",
            )
        if not isinstance(payload, dict):
            raise SubagentStreamSerializationError(
                "Runtime event serializer did not produce an object",
            )
        try:
            json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SubagentStreamSerializationError(
                "Runtime event is not JSON serializable",
            ) from exc
        return payload
