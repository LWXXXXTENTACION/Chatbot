"""A bounded, acyclic LangGraph workflow for web research."""

import asyncio
import json
import logging
import re
from typing import Any, TypedDict
from urllib.parse import urldefrag

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import StreamWriter

from app.config import DeepSeekModelId
from app.graph.model import emit, message_text
from app.graph.state import SourceCitation
from app.llm.client import create_deepseek_chat
from app.tools.web_search import web_search

logger = logging.getLogger("chatbot.deep_search")
MAX_QUERIES = 3
MAX_SOURCES = 8


class DeepSearchInput(TypedDict):
    query: str
    focus: str
    model_id: DeepSeekModelId


class DeepSearchState(DeepSearchInput):
    queries: list[str]
    search_outputs: list[dict[str, Any]]
    results: list[SourceCitation]
    summary: str


class DeepSearchOutput(TypedDict):
    query: str
    queries: list[str]
    summary: str
    results: list[SourceCitation]


def parse_search_queries(raw: str, fallback: str) -> list[str]:
    """Parse and de-duplicate up to three planned search queries."""
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


def dedupe_sources(search_outputs: list[Any]) -> list[SourceCitation]:
    """Flatten search results and remove duplicate or unsafe URLs."""
    sources: list[SourceCitation] = []
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


async def plan_queries_node(state: DeepSearchState) -> dict[str, Any]:
    """Use one model call to plan at most three complementary queries."""
    query = state["query"]
    focus = state.get("focus", "")
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore[assignment]
    llm = create_deepseek_chat(model_id, temperature=0.2)
    prompt = (
        f"研究问题：{query}\n研究重点：{focus or '无'}\n\n"
        "生成 1 到 3 个互补且精确的网络搜索词。只输出 JSON 字符串数组，"
        "不要解释。优先覆盖时效性、权威来源和问题中的不同关键角度。"
    )
    try:
        plan = await llm.ainvoke([
            SystemMessage(content="你负责为有界研究工作流规划最少量的可靠检索。"),
            HumanMessage(content=prompt),
        ])
        queries = parse_search_queries(message_text(plan), query)
    except Exception:
        logger.exception("Deep-search planning failed; using original query")
        queries = [query]
    return {"queries": queries}


async def search_sources_node(
    state: DeepSearchState,
    writer: StreamWriter,
) -> dict[str, Any]:
    """Run the planned searches concurrently and normalize their sources."""
    queries = state.get("queries", []) or [state["query"]]
    await emit(writer, {
        "type": "activity",
        "kind": "searching",
        "message": f"深度搜索：并行检索 {len(queries)} 个方向",
    })
    raw_outputs = await asyncio.gather(
        *(web_search.ainvoke({"query": query, "max_results": 4}) for query in queries),
        return_exceptions=True,
    )
    outputs = [output for output in raw_outputs if isinstance(output, dict)]
    sources = dedupe_sources(outputs)
    await emit(writer, {
        "type": "activity",
        "kind": "retrieved",
        "message": f"已整理 {len(sources)} 个不重复来源",
    })
    return {"search_outputs": outputs, "results": sources}


async def synthesize_brief_node(
    state: DeepSearchState,
    writer: StreamWriter,
) -> dict[str, Any]:
    """Create a citation-ready brief from only the collected evidence."""
    sources = state.get("results", [])
    if not sources:
        return {
            "summary": "没有检索到可用来源。请明确说明无法从搜索结果核验信息。",
        }

    await emit(writer, {
        "type": "activity",
        "kind": "analyzing",
        "message": "深度搜索工作流正在交叉整理证据",
    })
    evidence = "\n\n".join(
        f"[{index}] {source['title']}\nURL: {source['url']}\n摘要: {source['content']}"
        for index, source in enumerate(sources, start=1)
    )
    prompt = (
        f"用户问题：{state['query']}\n研究重点：{state.get('focus') or '无'}\n\n"
        f"来源：\n{evidence}\n\n"
        "写一份紧凑的研究简报供回答工作流使用。只依据来源，指出冲突或不确定性。"
        "每个事实句都必须在句末标点前紧跟 [[cite:1]] 或 [[cite:1,2]]；"
        "编号只能来自上面的来源，不要输出裸 URL 或另写参考资料列表。"
    )
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore[assignment]
    try:
        synthesis = await create_deepseek_chat(model_id, temperature=0.2).ainvoke([
            SystemMessage(content="你负责把检索证据整理成准确、可追溯的研究简报。"),
            HumanMessage(content=prompt),
        ])
        summary = message_text(synthesis).strip()
    except Exception:
        logger.exception("Deep-search synthesis failed")
        summary = "\n".join(
            f"- {source['title']}：{source['content']} [[cite:{index}]]"
            for index, source in enumerate(sources, start=1)
        )
    return {"summary": summary}


def build_deep_search_graph():
    """Compile the stateless plan → search → synthesize research DAG."""
    graph = StateGraph(
        DeepSearchState,
        input_schema=DeepSearchInput,
        output_schema=DeepSearchOutput,
    )
    graph.add_node("plan_queries", plan_queries_node)
    graph.add_node("search_sources", search_sources_node)
    graph.add_node("synthesize_brief", synthesize_brief_node)
    graph.add_edge(START, "plan_queries")
    graph.add_edge("plan_queries", "search_sources")
    graph.add_edge("search_sources", "synthesize_brief")
    graph.add_edge("synthesize_brief", END)
    return graph.compile(checkpointer=False, name="deep_search_workflow")


DEEP_SEARCH_GRAPH = build_deep_search_graph()


async def run_deep_search_workflow(
    *,
    query: str,
    focus: str,
    model_id: DeepSeekModelId,
    writer: StreamWriter,
) -> dict[str, Any]:
    """Invoke the inspectable research DAG and expose its stable tool output."""
    result: dict[str, Any] | None = None
    async for part in DEEP_SEARCH_GRAPH.astream(
        {"query": query, "focus": focus, "model_id": model_id},
        stream_mode=["values", "custom"],
        version="v2",
    ):
        if part["type"] == "custom":
            writer(part["data"])
        elif part["type"] == "values":
            result = part["data"]
    if result is None:
        raise RuntimeError("Deep-search graph completed without a final state")
    return {
        "query": result["query"],
        "queries": result.get("queries", [query]),
        "summary": result.get("summary", ""),
        "results": result.get("results", []),
    }
