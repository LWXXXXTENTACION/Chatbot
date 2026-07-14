"""Tool schema used by the main agent to delegate web research."""

from langchain_core.tools import tool


@tool
def deep_search(query: str, focus: str = "") -> dict:
    """委派给独立的深度搜索 Agent，返回带编号来源的研究摘要。

    仅在答案需要实时、外部或需要核验的信息时调用。普通知识、计算、写作和
    编程问题不要调用。一次用户请求最多调用一次；需要覆盖多个角度时，把它们
    合并进 query，并可用 focus 说明重点。

    Args:
        query: 要研究的完整问题。
        focus: 可选的研究范围、时间或来源偏好。
    """
    # The graph intercepts this tool call and delegates it to deep_search_agent.
    # Keeping a harmless implementation makes the schema valid for LangChain.
    return {"query": query, "focus": focus, "delegated": False}

