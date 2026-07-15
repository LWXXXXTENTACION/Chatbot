"""
Tool definitions for the LangGraph agent.
Each tool mirrors its TypeScript counterpart in src/lib/tools.ts.
"""

from app.tools.weather import get_weather
from app.tools.calculate import calculate
from app.tools.artifact import create_artifact
from app.tools.deep_search import deep_search
from app.tools.web_search import web_search

STANDARD_TOOLS = [get_weather, calculate, create_artifact]
FAST_SEARCH_TOOLS = [*STANDARD_TOOLS, web_search]
DEEP_SEARCH_MODE_TOOLS = [*STANDARD_TOOLS, deep_search]
# Automatic mode keeps the existing behavior: the main agent may delegate to
# deep search when it determines that external verification is necessary.
MAIN_AGENT_TOOLS = DEEP_SEARCH_MODE_TOOLS
DEEP_SEARCH_TOOLS = [web_search]

# Compatibility alias for code that expects an all-tools collection.
ALL_TOOLS = MAIN_AGENT_TOOLS

__all__ = [
    "get_weather",
    "calculate",
    "create_artifact",
    "deep_search",
    "web_search",
    "STANDARD_TOOLS",
    "FAST_SEARCH_TOOLS",
    "DEEP_SEARCH_MODE_TOOLS",
    "MAIN_AGENT_TOOLS",
    "DEEP_SEARCH_TOOLS",
    "ALL_TOOLS",
]
