"""Tool ownership for the specialized worker agents."""

from app.tools.weather import get_weather
from app.tools.calculate import calculate
from app.tools.artifact import create_artifact
from app.tools.deep_search import deep_search
from app.tools.web_search import web_search

STANDARD_TOOLS = [get_weather, calculate, create_artifact]
GENERAL_AGENT_TOOLS = STANDARD_TOOLS
RESEARCH_AGENT_TOOLS = [web_search, deep_search]

__all__ = [
    "get_weather",
    "calculate",
    "create_artifact",
    "deep_search",
    "web_search",
    "STANDARD_TOOLS",
    "GENERAL_AGENT_TOOLS",
    "RESEARCH_AGENT_TOOLS",
]
