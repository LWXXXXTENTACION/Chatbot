"""A bounded, independent agent for web research."""

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urldefrag

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.config import DeepSeekModelId
from app.llm.client import create_deepseek_chat
from app.tools.web_search import web_search

logger = logging.getLogger("chatbot.deep_search")

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
MAX_QUERIES = 3
MAX_SOURCES = 8


def _message_text(message: BaseMessage) -> str:
    """Return plain text from a LangChain message."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def parse_search_queries(raw: str, fallback: str) -> list[str]:
    """Parse and de-duplicate up to three search queries from model output."""
    candidates: list[Any] = []
    match = re.search(r"\[[\s\S]*?\]", raw)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                candidates = parsed
        except json.JSONDecodeError:
            pass

    if not candidates:
        candidates = [line.lstrip("-• 0123456789.").strip() for line in raw.splitlines()]

    queries: list[str] = []
    seen: set[str] = set()
    for candidate in [*candidates, fallback]:
        query = str(candidate).strip()
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            queries.append(query)
        if len(queries) == MAX_QUERIES:
            break
    return queries


def dedupe_sources(search_outputs: list[Any]) -> list[dict[str, Any]]:
    """Flatten search results and remove duplicate or invalid URLs."""
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for output in search_outputs:
        if not isinstance(output, dict):
            continue
        for item in output.get("results", []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            normalized, _ = urldefrag(url.rstrip("/"))
            if not normalized.startswith(("http://", "https://")) or normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            sources.append({
                "title": str(item.get("title", "")).strip() or normalized,
                "url": url,
                "content": str(item.get("content", "")).strip(),
                "score": item.get("score", 0),
            })
            if len(sources) == MAX_SOURCES:
                return sources
    return sources


async def _emit(callback: EventCallback | None, event: dict[str, Any]) -> None:
    if callback:
        await callback(event)


async def deep_search_agent(
    query: str,
    focus: str,
    model_id: DeepSeekModelId,
    callback: EventCallback | None = None,
) -> dict[str, Any]:
    """Plan searches, collect evidence, and synthesize a citation-ready brief."""
    llm = create_deepseek_chat(model_id, temperature=0.2)
    planning_prompt = (
        f"研究问题：{query}\n研究重点：{focus or '无'}\n\n"
        "生成 1 到 3 个互补且精确的网络搜索词。只输出 JSON 字符串数组，"
        "不要解释。优先覆盖时效性、权威来源和问题中的不同关键角度。"
    )
    try:
        plan = await llm.ainvoke([
            SystemMessage(content="你是深度搜索 Agent，负责用最少查询获得可靠证据。"),
            HumanMessage(content=planning_prompt),
        ])
        queries = parse_search_queries(_message_text(plan), query)
    except Exception:
        logger.exception("Deep-search planning failed; using the original query")
        queries = [query]

    await _emit(callback, {
        "type": "activity",
        "kind": "searching",
        "message": f"深度搜索：并行检索 {len(queries)} 个方向",
    })
    outputs = await asyncio.gather(
        *(web_search.ainvoke({"query": item, "max_results": 4}) for item in queries),
        return_exceptions=True,
    )
    successful = [output for output in outputs if not isinstance(output, BaseException)]
    sources = dedupe_sources(successful)
    await _emit(callback, {
        "type": "activity",
        "kind": "retrieved",
        "message": f"已整理 {len(sources)} 个不重复来源",
    })

    if not sources:
        return {
            "query": query,
            "queries": queries,
            "summary": "没有检索到可用来源。请明确说明无法从搜索结果核验信息。",
            "results": [],
        }

    evidence = "\n\n".join(
        f"[{index}] {source['title']}\nURL: {source['url']}\n摘要: {source['content']}"
        for index, source in enumerate(sources, start=1)
    )
    await _emit(callback, {
        "type": "activity",
        "kind": "analyzing",
        "message": "深度搜索 Agent 正在交叉整理证据",
    })
    synthesis_prompt = (
        f"用户问题：{query}\n研究重点：{focus or '无'}\n\n来源：\n{evidence}\n\n"
        "写一份紧凑的研究简报供主 Agent 使用。只依据来源，指出冲突或不确定性。"
        "每个事实后必须使用 [[cite:1]] 或 [[cite:1,2]]，编号只能来自上面的来源。"
        "不要另写参考资料列表。"
    )
    try:
        synthesis = await llm.ainvoke([
            SystemMessage(content="你是独立的深度搜索 Agent；准确性和可追溯性优先。"),
            HumanMessage(content=synthesis_prompt),
        ])
        summary = _message_text(synthesis).strip()
    except Exception:
        logger.exception("Deep-search synthesis failed")
        summary = "\n".join(
            f"- {source['title']}：{source['content']} [[cite:{index}]]"
            for index, source in enumerate(sources, start=1)
        )

    return {
        "query": query,
        "queries": queries,
        "summary": summary,
        "results": sources,
    }

