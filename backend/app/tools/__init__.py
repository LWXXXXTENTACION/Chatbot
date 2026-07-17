"""Tool ownership for the specialized worker agents."""

from app.tools.weather import get_weather
from app.tools.calculate import calculate
from app.tools.artifact import create_artifact
from app.tools.deep_search import deep_search
from app.tools.web_search import web_search
from app.tools.registry import TOOL_REGISTRY

GENERAL_AGENT_TOOLS = TOOL_REGISTRY.tools_for("general_agent")
RESEARCH_AGENT_TOOLS = TOOL_REGISTRY.tools_for("research_agent")
STANDARD_TOOLS = GENERAL_AGENT_TOOLS

__all__ = [
    "get_weather",
    "calculate",
    "create_artifact",
    "deep_search",
    "web_search",
    "STANDARD_TOOLS",
    "GENERAL_AGENT_TOOLS",
    "RESEARCH_AGENT_TOOLS",
    "TOOL_REGISTRY",
]
