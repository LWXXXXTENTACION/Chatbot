import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base, Conversation, Message, User
from app.database.observability import (
    aggregate_versions,
    list_run_traces,
    persist_run_trace,
    update_run_evaluation,
)
from app.observability import CODE_FINGERPRINT, TraceCollector


def make_trace(*, version_id: str = "model:agent-test", status: str = "success"):
    return {
        "schema_version": 1,
        "run_id": "r" * 32,
        "version": {
            "id": version_id,
            "label": "agent-test",
            "code_fingerprint": "a" * 64,
        },
        "conversation_id": "c" * 32,
        "user_message_id": "m" * 32,
        "model": "deepseek-v4-flash",
        "search_mode": "auto",
        "status": status,
        "started_at": "2026-07-16T10:00:00+00:00",
        "completed_at": "2026-07-16T10:00:01+00:00",
        "duration_ms": 1000,
        "metrics": {
            "input_tokens": 80,
            "output_tokens": 20,
            "total_tokens": 100,
            "llm_calls": 2,
            "tool_calls": 1,
            "tool_errors": 0,
            "cache_hits": 0,
            "checkpoint_hot_hits": 1,
            "checkpoint_durable_reads": 1,
            "checkpoint_writes": 2,
            "checkpoint_read_ms": 1.6,
            "checkpoint_write_ms": 2.5,
            "sources": 0,
        },
        "context": {},
        "timeline": [],
        "evaluation": None,
    }


def test_trace_collector_counts_model_tool_context_and_tokens():
    collector = TraceCollector(
        conversation_id="conversation",
        user_message_id="message",
        model="deepseek-v4-flash",
        search_mode="web",
    )
    collector.observe_event({
        "type": "context_status",
        "strategies": ["microcompact"],
        "estimatedTokensBefore": 1200,
        "estimatedTokensAfter": 800,
        "maxTokens": 32000,
        "removedMessages": 1,
        "compactedToolResults": 2,
        "retrievedContextTokens": 120,
        "overflowed": False,
    })
    collector.observe_event({
        "type": "context_retrieval",
        "status": "ok",
        "candidateCount": 8,
        "returnedCount": 2,
        "tokenCount": 120,
        "topScore": 0.91,
        "durationMs": 14,
        "indexVersion": "v1",
        "nodeIds": ["n1", "n2"],
    })
    collector.observe_event({
        "type": "context_index",
        "status": "ok",
        "documentCount": 1,
        "indexedNodeCount": 2,
        "skippedDocumentCount": 0,
        "durationMs": 8,
        "indexVersion": "v1",
    })
    collector.observe_event({
        "type": "tool_call_start",
        "toolCallId": "tool-1",
        "toolName": "web_search",
    })
    collector.observe_event({
        "type": "tool_result",
        "toolCallId": "tool-1",
        "cached": True,
        "cacheLayer": "l1",
        "error": None,
        "durationMs": 25,
        "outputChars": 100,
        "modelOutputChars": 80,
        "outputTruncated": True,
    })
    collector.model_started("llm-1", model_name="deepseek-v4-flash")
    result = LLMResult(generations=[[
        ChatGeneration(message=AIMessage(
            content="done",
            usage_metadata={
                "input_tokens": 90,
                "output_tokens": 10,
                "total_tokens": 100,
            },
        ))
    ]])
    collector.model_finished("llm-1", response=result)
    collector.observe_checkpoint(
        operation="read", source="durable", duration_ms=1.5
    )
    collector.observe_checkpoint(
        operation="read", source="hot", duration_ms=0.1
    )
    collector.observe_checkpoint(
        operation="write", source="durable", duration_ms=2.0
    )

    trace = collector.finish("success")

    assert len(CODE_FINGERPRINT) == 64
    tool_event = next(
        item for item in trace["timeline"] if item["type"] == "tool.end"
    )
    assert tool_event["metadata"]["cache_layer"] == "l1"
    assert trace["metrics"] == {
        "input_tokens": 90,
        "output_tokens": 10,
        "total_tokens": 100,
        "llm_calls": 1,
        "tool_calls": 1,
        "tool_errors": 0,
        "tool_rejections": 0,
        "tool_timeouts": 0,
        "tool_duration_ms": 25,
        "tool_output_chars": 100,
        "tool_truncations": 1,
        "cache_hits": 1,
        "checkpoint_hot_hits": 1,
        "checkpoint_durable_reads": 1,
        "checkpoint_writes": 1,
        "checkpoint_history_reads": 0,
        "checkpoint_read_ms": 1.6,
        "checkpoint_write_ms": 2.0,
        "checkpoint_hot_hit_rate": 0.5,
        "context_retrieval_calls": 1,
        "context_retrieval_errors": 0,
        "context_retrieved_chunks": 2,
        "context_retrieved_tokens": 120,
        "context_retrieval_ms": 14,
        "context_index_calls": 1,
        "context_index_errors": 0,
        "context_indexed_documents": 1,
        "context_indexed_nodes": 2,
        "context_index_skipped_documents": 0,
        "context_index_ms": 8,
        "sources": 0,
    }
    assert trace["context"]["estimated_tokens_after"] == 800
    assert {event["type"] for event in trace["timeline"]} >= {
        "context",
        "tool.start",
        "tool.end",
        "llm.start",
        "llm.end",
        "checkpoint.summary",
        "context.retrieval",
        "context.index",
    }


@pytest.mark.asyncio
async def test_run_traces_are_owner_scoped_evaluable_and_aggregated(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'traces.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        db.add_all([
            User(id="u" * 32, username="owner", password_hash="hash"),
            User(id="x" * 32, username="other", password_hash="hash"),
            Conversation(id="c" * 32, user_id="u" * 32, title="owner run"),
            Conversation(id="d" * 32, user_id="x" * 32, title="other run"),
            Message(
                id="m" * 32,
                conversation_id="c" * 32,
                role="user",
                sequence=0,
            ),
        ])
        await db.commit()
        trace = make_trace()
        await persist_run_trace(db, "m" * 32, trace)

        assert await list_run_traces(db, "x" * 32) == []
        owner_traces = await list_run_traces(db, "u" * 32)
        assert len(owner_traces) == 1
        assert owner_traces[0]["conversation"]["title"] == "owner run"

        updated = await update_run_evaluation(
            db,
            "u" * 32,
            "r" * 32,
            passed=True,
            note="meets rubric",
            case_id="case-01",
        )
        assert updated is not None
        assert updated["evaluation"]["passed"] is True
        assert await update_run_evaluation(
            db,
            "x" * 32,
            "r" * 32,
            passed=False,
            note="",
            case_id="",
        ) is None

        versions = aggregate_versions([updated])
        assert versions[0]["runs"] == 1
        assert versions[0]["avg_tokens"] == 100
        assert versions[0]["pass_rate"] == 1.0
        assert versions[0]["checkpoint_hot_hit_rate"] == 0.5
        assert versions[0]["avg_checkpoint_read_ms"] == 0.8
        assert versions[0]["avg_checkpoint_write_ms"] == 1.25
        assert versions[0]["categories"][0]["label"] == "普通对话"

    await engine.dispose()
