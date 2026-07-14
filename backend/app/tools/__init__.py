"""
Tool definitions for the LangGraph agent.
Each tool mirrors its TypeScript counterpart in src/lib/tools.ts.
"""

from langchain_core.tools import tool

from app.tools.weather import get_weather
from app.tools.calculate import calculate
from app.tools.artifact import create_artifact
from app.tools.web_search import web_search

# All tools available to the agent
ALL_TOOLS = [get_weather, calculate, create_artifact, web_search]

__all__ = ["get_weather", "calculate", "create_artifact", "web_search", "ALL_TOOLS"]
