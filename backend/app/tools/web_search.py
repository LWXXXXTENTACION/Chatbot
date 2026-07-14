"""
Web search tool using DuckDuckGo (free, no API key required).
Provides real-time web search with structured results for citation.
"""

from langchain_core.tools import tool
from ddgs import DDGS


@tool
def web_search(query: str, max_results: int = 5) -> dict:
    """搜索互联网获取最新信息。当你需要事实、最新新闻、或训练数据覆盖不到的知识时使用。

    重要：使用此工具后，必须在文本回答中用 [1]、[2] 等序号标注引用来源，
    序号对应返回结果数组的索引顺序。

    Args:
        query: 搜索关键词
        max_results: 最多返回结果数（默认 5，上限 5）
    """
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=min(max_results, 5)))

    return {
        "query": query,
        "results": [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "content": r.get("body", ""),
                "score": 0,
            }
            for r in results
        ],
    }
