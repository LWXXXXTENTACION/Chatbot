"""
Tool definitions for the LangGraph agent.
Each tool mirrors its TypeScript counterpart in src/lib/tools.ts.
"""

from app.tools.weather import get_weather
from app.tools.calculate import calculate
from app.tools.artifact import create_artifact
from app.tools.deep_search import deep_search
from app.tools.web_search import web_search

# The main agent never calls raw web search. It delegates research through the
# deep_search tool, which is executed by the independent deep-search agent.
STANDARD_TOOLS = [get_weather, calculate, create_artifact]
MAIN_AGENT_TOOLS = [*STANDARD_TOOLS, deep_search]
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
    "MAIN_AGENT_TOOLS",
    "DEEP_SEARCH_TOOLS",
    "ALL_TOOLS",
]
