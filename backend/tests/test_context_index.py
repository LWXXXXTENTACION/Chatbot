"""语义索引只保存安全历史，并以显式 LangGraph 节点 fail-open。"""

from types import SimpleNamespace
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.context_index.service import (
    ContextIndexHit,
    ContextIndexService,
    ContextIndexWriteResult,
    render_archive_turn,
    stable_document_id,
    stable_node_id,
)
from app.database.models import Message, MessagePart
from app.graph.context import AgentRuntimeContext
from app.graph.context_index_nodes import archive_context_node, retrieve_context_node


def _state(**overrides):
    value = {
        "messages": [HumanMessage(content="项目审阅人是谁？", id="current")],
        "model_id": "deepseek-v4-flash",
        "system_prompt": "",
        "user_id": "user-a",
        "conversation_id": "conv-a",
        "retrieved_context": [],
        "context_archive_queue": [],
    }
    value.update(overrides)
    return value


def test_stable_ids_and_safe_archive_rendering():
    first = stable_document_id("u", "c", "h", "a")
    assert first == stable_document_id("u", "c", "h", "a")
    assert first != stable_document_id("other", "c", "h", "a")
    first_node = stable_node_id(first, 0, "v1")
    assert first_node == stable_node_id(first, 0, "v1")
    assert first_node != stable_node_id(first, 1, "v1")
    assert str(UUID(first_node)) == first_node

    user = Message(id="h", conversation_id="c", role="user", sequence=1)
    user.parts = [MessagePart(type="text", text="请生成风险报告", position=0)]
    assistant = Message(id="a", conversation_id="c", role="assistant", sequence=2)
    assistant.parts = [
        MessagePart(type="text", text="报告已完成", position=0),
        MessagePart(type="reasoning", text="secret chain of thought", position=1),
        MessagePart(
            type="sources",
            tool_output={"results": [{
                "title": "可信来源", "url": "https://secret", "content": "raw"
            }]},
            position=2,
        ),
        MessagePart(
            type="tool-create_artifact",
            tool_input={
                "title": "风险手册", "kind": "html", "content": "<html>FULL</html>"
            },
            position=3,
        ),
    ]
    text, ids = render_archive_turn([user, assistant])
    assert ids == ("h", "a")
    assert "可信来源" in text and "风险手册" in text
    assert "secret chain" not in text
    assert "https://secret" not in text and "<html>FULL</html>" not in text


@pytest.mark.asyncio
async def test_retrieve_node_filters_score_duplicates_and_active_messages():
    class FakeService:
        enabled = True

        async def retrieve(self, **kwargs):
            assert kwargs["user_id"] == "user-a"
            assert kwargs["conversation_id"] == "conv-a"
            return [
                ContextIndexHit("good", "审阅人为李明", 0.9, ("old-h", "old-a")),
                ContextIndexHit("low", "低分", 0.1, ("x", "y")),
                ContextIndexHit("active", "当前", 0.99, ("current",)),
                ContextIndexHit("good", "重复", 0.8, ("z",)),
            ]

    events = []
    runtime = SimpleNamespace(context=AgentRuntimeContext(context_index=FakeService()))
    update = await retrieve_context_node(_state(), runtime, events.append)
    assert [item["node_id"] for item in update["retrieved_context"]] == ["good"]
    assert events[0]["type"] == "context_retrieval"
    assert events[0]["candidateCount"] == 4
    assert events[0]["returnedCount"] == 1
    assert "query" not in events[0]


@pytest.mark.asyncio
async def test_archive_node_consumes_refs_and_both_nodes_fail_open():
    class FakeService:
        enabled = True

        async def archive_refs(self, **kwargs):
            assert kwargs["refs"][0]["start_message_id"] == "old-h"
            return ContextIndexWriteResult(1, 2, 0, 5)

        async def retrieve(self, **_kwargs):
            raise RuntimeError("qdrant unavailable")

    service = FakeService()
    runtime = SimpleNamespace(context=AgentRuntimeContext(context_index=service))
    events = []
    archived = await archive_context_node(_state(context_archive_queue=[{
        "start_message_id": "old-h", "end_message_id": "old-a"
    }]), runtime, events.append)
    assert archived == {"context_archive_queue": []}
    assert events[0]["indexedNodeCount"] == 2

    retrieval_events = []
    retrieved = await retrieve_context_node(_state(), runtime, retrieval_events.append)
    assert retrieved == {"retrieved_context": []}
    assert retrieval_events[0]["status"] == "error"


@pytest.mark.asyncio
async def test_empty_index_never_loads_or_downloads_embedding_model(monkeypatch):
    """新会话没有向量可召回时，普通聊天不应触发 Hugging Face 初始化。"""
    service = ContextIndexService(
        enabled=True,
        path="./unused-test-index",
        collection="chat_context_test",
        embed_model_name="BAAI/bge-small-zh-v1.5",
        index_version="test",
        session_factory=None,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(service, "_has_collection_sync", lambda: False)

    async def unexpected_model_load():
        raise AssertionError("空索引不应加载嵌入模型")

    monkeypatch.setattr(service, "_ensure_loaded", unexpected_model_load)
    hits = await service.retrieve(
        user_id="user-a",
        conversation_id="new-conversation",
        query="Transformer 是什么？",
        top_k=8,
    )
    assert hits == []
