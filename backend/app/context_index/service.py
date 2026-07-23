"""LlamaIndex + 本地 Qdrant 的历史 turn 归档与召回服务。

业务数据库是事实来源，Qdrant 只是可删除、可重建的派生索引。模块顶层不导入
LlamaIndex/HuggingFace，避免可选依赖或模型下载失败阻断 FastAPI 启动。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.database.models import Conversation, Message, MessagePart
from app.graph.state import ContextArchiveRef

logger = logging.getLogger("chatbot.context_index")
CHUNK_SIZE = 384
CHUNK_OVERLAP = 48
LOAD_RETRY_SECONDS = 30.0


def _digest(*values: str) -> str:
    encoded = "\x1f".join(values).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_document_id(
    user_id: str,
    conversation_id: str,
    first_message_id: str,
    last_message_id: str,
) -> str:
    return _digest(user_id, conversation_id, first_message_id, last_message_id)


def stable_node_id(document_id: str, chunk_index: int, index_version: str) -> str:
    """生成 Qdrant 可接受的确定性 UUID。

    本地 Qdrant 的 point id 只能是整数或标准 UUID，不能直接使用 64 位 SHA-256。
    UUIDv5 同样由稳定输入决定：重复构建会得到同一个 ID，同时保留版本隔离。
    """
    identity = "\x1f".join((document_id, str(chunk_index), index_version))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


@dataclass(frozen=True, slots=True)
class ContextIndexDocument:
    doc_id: str
    text: str
    message_ids: tuple[str, ...]
    created_at: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ContextIndexHit:
    node_id: str
    text: str
    score: float
    message_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextIndexWriteResult:
    documents: int = 0
    indexed_nodes: int = 0
    skipped_documents: int = 0
    duration_ms: int = 0


def _part_text(parts: list[MessagePart]) -> str:
    return "".join(part.text or "" for part in parts if part.type == "text").strip()


def _source_titles(part: MessagePart) -> list[str]:
    payload = part.tool_output
    results = payload.get("results", []) if isinstance(payload, dict) else []
    return [
        str(item.get("title", "")).strip()
        for item in results
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]


def _artifact_label(part: MessagePart) -> str:
    """只保留 Artifact 标题/类型，绝不把完整 HTML、PDF 预览或工具 JSON 入库。"""
    if part.type != "tool-create_artifact":
        return ""
    args = part.tool_input if isinstance(part.tool_input, dict) else {}
    title = str(args.get("title", "Artifact")).strip() or "Artifact"
    kind = str(args.get("kind", "document")).strip() or "document"
    return f"Artifact：{title}（{kind}）"


def render_archive_turn(rows: list[Message]) -> tuple[str, tuple[str, ...]]:
    """从可信业务行生成最小语义文档，过滤 reasoning 与原始工具 payload。"""
    chunks: list[str] = []
    message_ids: list[str] = []
    for row in sorted(rows, key=lambda item: item.sequence):
        parts = sorted(row.parts or [], key=lambda part: part.position)
        text = _part_text(parts)
        if row.role == "user" and text:
            chunks.append(f"用户：{text}")
            message_ids.append(str(row.id))
        elif row.role == "assistant":
            assistant_chunks: list[str] = []
            if text:
                assistant_chunks.append(f"助手：{text}")
            titles = [
                title
                for part in parts if part.type == "sources"
                for title in _source_titles(part)
            ]
            if titles:
                assistant_chunks.append(
                    "来源标题：" + "；".join(dict.fromkeys(titles))
                )
            artifacts = [label for part in parts if (label := _artifact_label(part))]
            assistant_chunks.extend(artifacts)
            if assistant_chunks:
                chunks.extend(assistant_chunks)
                message_ids.append(str(row.id))
    return "\n\n".join(chunks).strip(), tuple(message_ids)


class ContextIndexService:
    """进程级语义索引服务；同步本地计算全部放到 worker thread。"""

    def __init__(
        self,
        *,
        enabled: bool,
        path: str,
        collection: str,
        embed_model_name: str,
        allow_model_download: bool = False,
        index_version: str,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.enabled = enabled
        self.path = str(Path(path).expanduser().resolve())
        self.collection = collection
        self.embed_model_name = embed_model_name
        self.allow_model_download = allow_model_download
        self.index_version = index_version
        self.session_factory = session_factory
        self._write_lock = asyncio.Lock()
        self._load_lock = asyncio.Lock()
        self._loaded = False
        self._load_retry_at = 0.0
        self._client: Any | None = None
        self._vector_store: Any | None = None
        self._embed_model: Any | None = None
        self._pipeline: Any | None = None
        self._index: Any | None = None

    def _has_collection_sync(self) -> bool:
        """轻量检查是否已有可检索数据，不加载 Torch 或嵌入模型。

        新会话和空索引没有任何内容可召回。先做这一步可避免第一次聊天为了一个
        必然为空的结果下载数百 MB 模型，尤其重要的是它不会阻塞 LangGraph 热路径。
        """
        from qdrant_client import QdrantClient

        Path(self.path).mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=self.path)
        try:
            return client.collection_exists(self.collection)
        finally:
            client.close()

    async def _ensure_loaded(self) -> bool:
        if not self.enabled:
            return False
        if self._loaded:
            return True
        if time.monotonic() < self._load_retry_at:
            return False
        async with self._load_lock:
            if self._loaded:
                return True
            try:
                await asyncio.to_thread(self._load_sync)
                self._loaded = True
                return True
            except Exception:
                self._load_retry_at = time.monotonic() + LOAD_RETRY_SECONDS
                logger.exception(
                    "Context index unavailable; conversation continues without retrieval"
                )
                return False

    def _load_sync(self) -> None:
        from llama_index.core import VectorStoreIndex
        from llama_index.core.ingestion import IngestionPipeline
        from llama_index.core.node_parser import SentenceSplitter
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient

        Path(self.path).mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=self.path)
        try:
            vector_store = QdrantVectorStore(
                client=client,
                collection_name=self.collection,
            )
            embed_model = HuggingFaceEmbedding(
                model_name=self.embed_model_name,
                trust_remote_code=False,
                # Web 请求不负责下载模型。若权重尚未由 index:context 准备好，
                # Hugging Face 会快速抛错，_ensure_loaded 随即 fail-open。
                local_files_only=not self.allow_model_download,
            )

            def node_id(chunk_index: int, document: Any) -> str:
                return stable_node_id(
                    str(document.id_), chunk_index, self.index_version
                )

            pipeline = IngestionPipeline(
                transformations=[
                    SentenceSplitter(
                        chunk_size=CHUNK_SIZE,
                        chunk_overlap=CHUNK_OVERLAP,
                        id_func=node_id,
                    ),
                    embed_model,
                ],
                vector_store=vector_store,
            )
            self._client = client
            self._vector_store = vector_store
            self._embed_model = embed_model
            self._pipeline = pipeline
            self._index = VectorStoreIndex.from_vector_store(
                vector_store,
                embed_model=embed_model,
            )
        except Exception:
            # 本地模型缺失等初始化失败不能遗留 Qdrant 文件锁，否则下一轮轻量
            # collection 检查也会失败，表现成语义索引永久不可恢复。
            client.close()
            raise

    async def _load_documents(
        self,
        user_id: str,
        conversation_id: str,
        refs: list[ContextArchiveRef],
    ) -> list[ContextIndexDocument]:
        documents: list[ContextIndexDocument] = []
        async with self.session_factory() as db:
            owner = await db.scalar(select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            ))
            if owner is None:
                return []
            for ref in refs:
                boundary_result = await db.execute(
                    select(Message).where(
                        Message.conversation_id == conversation_id,
                        Message.id.in_([
                            ref["start_message_id"],
                            ref["end_message_id"],
                        ]),
                    )
                )
                boundaries = {str(row.id): row for row in boundary_result.scalars()}
                start = boundaries.get(ref["start_message_id"])
                end = boundaries.get(ref["end_message_id"])
                if (
                    start is None or end is None
                    or start.role != "user" or end.role != "assistant"
                    or start.sequence > end.sequence
                ):
                    continue
                result = await db.execute(
                    select(Message)
                    .options(selectinload(Message.parts))
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.sequence >= start.sequence,
                        Message.sequence <= end.sequence,
                    )
                    .order_by(Message.sequence)
                )
                rows = list(result.scalars().all())
                text, message_ids = render_archive_turn(rows)
                if not text or not message_ids:
                    continue
                doc_id = stable_document_id(
                    user_id,
                    conversation_id,
                    str(start.id),
                    str(end.id),
                )
                created = start.created_at.replace(
                    tzinfo=start.created_at.tzinfo or timezone.utc
                ).astimezone(timezone.utc).isoformat()
                documents.append(ContextIndexDocument(
                    doc_id=doc_id,
                    text=text,
                    message_ids=message_ids,
                    created_at=created,
                    content_hash=_digest(text, self.index_version),
                ))
        return documents

    def _metadata_filter(self, user_id: str, conversation_id: str) -> Any:
        from qdrant_client import models

        return models.Filter(must=[
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=user_id)
            ),
            models.FieldCondition(
                key="conversation_id", match=models.MatchValue(value=conversation_id)
            ),
            models.FieldCondition(
                key="index_version",
                match=models.MatchValue(value=self.index_version),
            ),
        ])

    def _existing_hash_sync(self, doc_id: str) -> str | None:
        from qdrant_client import models

        if not self._client.collection_exists(self.collection):
            return None
        records, _ = self._client.scroll(
            collection_name=self.collection,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="archive_doc_id",
                match=models.MatchValue(value=doc_id),
            )]),
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not records or not isinstance(records[0].payload, dict):
            return None
        return str(records[0].payload.get("content_hash") or "") or None

    def _delete_filter_sync(self, metadata_filter: Any) -> None:
        from qdrant_client import models

        if not self._client.collection_exists(self.collection):
            return
        self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(filter=metadata_filter),
            wait=True,
        )

    def _archive_sync(
        self,
        user_id: str,
        conversation_id: str,
        documents: list[ContextIndexDocument],
    ) -> tuple[int, int]:
        from llama_index.core import Document
        from qdrant_client import models

        indexed_nodes = 0
        skipped = 0
        for item in documents:
            if self._existing_hash_sync(item.doc_id) == item.content_hash:
                skipped += 1
                continue
            self._delete_filter_sync(models.Filter(must=[models.FieldCondition(
                key="archive_doc_id",
                match=models.MatchValue(value=item.doc_id),
            )]))
            document = Document(
                text=item.text,
                id_=item.doc_id,
                metadata={
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "message_ids": list(item.message_ids),
                    "archive_doc_id": item.doc_id,
                    "content_hash": item.content_hash,
                    "created_at": item.created_at,
                    "index_version": self.index_version,
                },
                excluded_llm_metadata_keys=["content_hash", "index_version"],
                excluded_embed_metadata_keys=["content_hash", "index_version"],
            )
            nodes = self._pipeline.run(documents=[document], show_progress=False)
            indexed_nodes += len(nodes)
        return indexed_nodes, skipped

    async def archive_refs(
        self,
        *,
        user_id: str,
        conversation_id: str,
        refs: list[ContextArchiveRef],
    ) -> ContextIndexWriteResult:
        started = time.perf_counter()
        if not refs or not await self._ensure_loaded():
            return ContextIndexWriteResult(
                duration_ms=round((time.perf_counter() - started) * 1000)
            )
        documents = await self._load_documents(user_id, conversation_id, refs)
        async with self._write_lock:
            indexed, skipped = await asyncio.to_thread(
                self._archive_sync,
                user_id,
                conversation_id,
                documents,
            )
        return ContextIndexWriteResult(
            documents=len(documents),
            indexed_nodes=indexed,
            skipped_documents=skipped,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _retrieve_sync(
        self,
        user_id: str,
        conversation_id: str,
        query: str,
        top_k: int,
    ) -> list[ContextIndexHit]:
        from llama_index.core.vector_stores import (
            FilterOperator,
            MetadataFilter,
            MetadataFilters,
        )

        if not self._client.collection_exists(self.collection):
            return []
        filters = MetadataFilters(filters=[
            MetadataFilter(
                key="user_id", value=user_id, operator=FilterOperator.EQ
            ),
            MetadataFilter(
                key="conversation_id",
                value=conversation_id,
                operator=FilterOperator.EQ,
            ),
            MetadataFilter(
                key="index_version",
                value=self.index_version,
                operator=FilterOperator.EQ,
            ),
        ])
        retriever = self._index.as_retriever(
            similarity_top_k=top_k,
            filters=filters,
        )
        hits: list[ContextIndexHit] = []
        for result in retriever.retrieve(query):
            node = result.node
            metadata = node.metadata if isinstance(node.metadata, dict) else {}
            message_ids = metadata.get("message_ids", [])
            hits.append(ContextIndexHit(
                node_id=str(node.node_id),
                text=str(node.get_content()).strip(),
                score=float(result.score or 0.0),
                message_ids=tuple(str(value) for value in message_ids),
            ))
        return hits

    async def retrieve(
        self,
        *,
        user_id: str,
        conversation_id: str,
        query: str,
        top_k: int,
    ) -> list[ContextIndexHit]:
        if not query.strip():
            return []
        if not self._loaded:
            # 没有 collection 时结果必为空，因此连 LlamaIndex/Torch 都无需初始化。
            # collection 存在但模型缺失时，local_files_only 会快速失败并放行聊天。
            has_collection = await asyncio.to_thread(self._has_collection_sync)
            if not has_collection or not await self._ensure_loaded():
                return []
        return await asyncio.to_thread(
            self._retrieve_sync,
            user_id,
            conversation_id,
            query,
            top_k,
        )

    async def delete_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> None:
        if not await self._ensure_loaded():
            return
        async with self._write_lock:
            await asyncio.to_thread(
                self._delete_filter_sync,
                self._metadata_filter(user_id, conversation_id),
            )

    async def rebuild_conversation(
        self,
        *,
        user_id: str,
        conversation_id: str,
    ) -> ContextIndexWriteResult:
        """从业务库重建所有已完成 turn；幂等判断会跳过未变化内容。"""
        refs: list[ContextArchiveRef] = []
        async with self.session_factory() as db:
            result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.sequence)
            )
            rows = list(result.scalars().all())
        current_user: Message | None = None
        last_assistant: Message | None = None
        for row in rows:
            if row.role == "user":
                if current_user is not None and last_assistant is not None:
                    refs.append({
                        "start_message_id": str(current_user.id),
                        "end_message_id": str(last_assistant.id),
                    })
                current_user = row
                last_assistant = None
            elif row.role == "assistant" and current_user is not None:
                last_assistant = row
        if current_user is not None and last_assistant is not None:
            refs.append({
                "start_message_id": str(current_user.id),
                "end_message_id": str(last_assistant.id),
            })
        return await self.archive_refs(
            user_id=user_id,
            conversation_id=conversation_id,
            refs=refs,
        )

    def _indexed_conversations_sync(self) -> set[tuple[str, str]]:
        if not self._client.collection_exists(self.collection):
            return set()
        pairs: set[tuple[str, str]] = set()
        offset = None
        while True:
            records, offset = self._client.scroll(
                collection_name=self.collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in records:
                payload = record.payload if isinstance(record.payload, dict) else {}
                user_id = str(payload.get("user_id") or "")
                conversation_id = str(payload.get("conversation_id") or "")
                if user_id and conversation_id:
                    pairs.add((user_id, conversation_id))
            if offset is None:
                break
        return pairs

    async def prune_orphans(self, valid_pairs: set[tuple[str, str]]) -> int:
        """删除业务库中已不存在的 conversation 向量，返回清理的会话数。"""
        if not await self._ensure_loaded():
            return 0
        async with self._write_lock:
            indexed = await asyncio.to_thread(self._indexed_conversations_sync)
            orphans = indexed - valid_pairs
            for user_id, conversation_id in orphans:
                await asyncio.to_thread(
                    self._delete_filter_sync,
                    self._metadata_filter(user_id, conversation_id),
                )
        return len(orphans)

    async def close(self) -> None:
        client = self._client
        self._loaded = False
        if client is not None:
            await asyncio.to_thread(client.close)
