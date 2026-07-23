"""语义历史的两个显式 LangGraph 节点：先召回，再归档压缩掉的旧 turn。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import StreamWriter

from app.config import (
    CONTEXT_INDEX_VERSION,
    CONTEXT_RETRIEVAL_MAX_CHUNKS,
    CONTEXT_RETRIEVAL_MAX_TOKENS,
    CONTEXT_RETRIEVAL_SCORE_THRESHOLD,
    CONTEXT_RETRIEVAL_TOP_K,
)
from app.graph.context import AgentRuntimeContext
from app.graph.context_window import estimate_tokens
from app.graph.events import emit
from app.graph.model import message_text
from app.graph.state import AgentState, RetrievedContextItem

logger = logging.getLogger("chatbot.graph.context_index")


async def retrieve_context_node(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
    writer: StreamWriter,
) -> dict[str, Any]:
    """按当前问题召回同一用户、同一 conversation 的旧历史，失败时返回空列表。"""
    started = time.perf_counter()
    service = runtime.context.context_index
    query_message = next(
        (
            message for message in reversed(state.get("messages", []))
            if isinstance(message, HumanMessage)
        ),
        None,
    )
    query = message_text(query_message).strip() if query_message is not None else ""
    status = "disabled" if service is None or not service.enabled else "ok"
    candidates = []
    selected: list[RetrievedContextItem] = []
    used_tokens = 0
    try:
        if service is not None and service.enabled and query:
            candidates = await service.retrieve(
                user_id=state.get("user_id", ""),
                conversation_id=state.get("conversation_id", ""),
                query=query,
                top_k=CONTEXT_RETRIEVAL_TOP_K,
            )
            active_ids = {
                str(message.id) for message in state.get("messages", []) if message.id
            }
            seen_nodes: set[str] = set()
            for hit in candidates:
                if hit.score < CONTEXT_RETRIEVAL_SCORE_THRESHOLD:
                    continue
                if hit.node_id in seen_nodes or active_ids.intersection(hit.message_ids):
                    continue
                item_tokens = estimate_tokens([HumanMessage(content=hit.text)])
                if used_tokens + item_tokens > CONTEXT_RETRIEVAL_MAX_TOKENS:
                    continue
                selected.append({
                    "node_id": hit.node_id,
                    "text": hit.text,
                    "score": hit.score,
                    "message_ids": list(hit.message_ids),
                })
                seen_nodes.add(hit.node_id)
                used_tokens += item_tokens
                if len(selected) >= CONTEXT_RETRIEVAL_MAX_CHUNKS:
                    break
        elif service is not None and service.enabled:
            status = "empty_query"
    except asyncio.CancelledError:
        raise
    except Exception:
        status = "error"
        candidates = []
        selected = []
        used_tokens = 0
        logger.exception("Context retrieval failed open")

    await emit(writer, {
        "type": "context_retrieval",
        "status": status,
        "candidateCount": len(candidates),
        "returnedCount": len(selected),
        "tokenCount": used_tokens,
        "topScore": round(max((item["score"] for item in selected), default=0.0), 4),
        "durationMs": round((time.perf_counter() - started) * 1000),
        "indexVersion": CONTEXT_INDEX_VERSION,
        "nodeIds": [item["node_id"] for item in selected],
    })
    return {"retrieved_context": selected}


async def archive_context_node(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
    writer: StreamWriter,
) -> dict[str, Any]:
    """消费轻量范围引用，再从业务库读取可信正文并幂等写入索引。"""
    started = time.perf_counter()
    service = runtime.context.context_index
    refs = list(state.get("context_archive_queue", []))
    status = "disabled" if service is None or not service.enabled else "ok"
    documents = indexed_nodes = skipped = 0
    try:
        if service is not None and service.enabled and refs:
            result = await service.archive_refs(
                user_id=state.get("user_id", ""),
                conversation_id=state.get("conversation_id", ""),
                refs=refs,
            )
            documents = result.documents
            indexed_nodes = result.indexed_nodes
            skipped = result.skipped_documents
        elif service is not None and service.enabled:
            status = "empty"
    except asyncio.CancelledError:
        raise
    except Exception:
        status = "error"
        logger.exception("Context indexing failed open")

    await emit(writer, {
        "type": "context_index",
        "status": status,
        "documentCount": documents,
        "indexedNodeCount": indexed_nodes,
        "skippedDocumentCount": skipped,
        "durationMs": round((time.perf_counter() - started) * 1000),
        "indexVersion": CONTEXT_INDEX_VERSION,
    })
    # 即使索引不可用也必须清空队列，避免引用在 checkpoint 中跨 turn 重试和膨胀。
    return {"context_archive_queue": []}
