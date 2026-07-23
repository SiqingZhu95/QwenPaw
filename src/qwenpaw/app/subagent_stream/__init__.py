# -*- coding: utf-8 -*-
"""Optional stream side channel for spawn_subagent."""

from .context import install_subagent_stream_toolkit_middleware
from .observer import SubagentStreamObserverFactory
from .tool_bridge import subagent_stream_tool_bridge

__all__ = [
    "SubagentStreamObserverFactory",
    "install_subagent_stream_toolkit_middleware",
    "subagent_stream_tool_bridge",
]
