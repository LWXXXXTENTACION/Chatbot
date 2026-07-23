#!/usr/bin/env python3
"""从 chatbot.db 重建可删除的语义上下文索引。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import (  # noqa: E402
    CONTEXT_EMBED_MODEL,
    CONTEXT_INDEX_COLLECTION,
    CONTEXT_INDEX_PATH,
    CONTEXT_INDEX_VERSION,
)
from app.context_index import ContextIndexService  # noqa: E402
from app.database.engine import async_session  # noqa: E402
from app.database.models import Conversation  # noqa: E402


async def run(options: argparse.Namespace) -> dict[str, int]:
    service = ContextIndexService(
        enabled=True,
        path=CONTEXT_INDEX_PATH,
        collection=CONTEXT_INDEX_COLLECTION,
        embed_model_name=CONTEXT_EMBED_MODEL,
        # 这是显式的离线维护命令，可以等待首次模型下载；在线聊天永远不做此事。
        allow_model_download=True,
        index_version=CONTEXT_INDEX_VERSION,
        session_factory=async_session,
    )
    totals = {"conversations": 0, "documents": 0, "indexed_nodes": 0,
              "skipped_documents": 0, "pruned_orphans": 0}
    try:
        async with async_session() as db:
            query = select(Conversation)
            if options.conversation_id:
                query = query.where(Conversation.id == options.conversation_id)
            if options.user_id:
                query = query.where(Conversation.user_id == options.user_id)
            conversations = list((await db.execute(query)).scalars().all())
        valid_pairs = {
            (str(item.user_id), str(item.id)) for item in conversations
        }
        for conversation in conversations:
            result = await service.rebuild_conversation(
                user_id=str(conversation.user_id),
                conversation_id=str(conversation.id),
            )
            totals["conversations"] += 1
            totals["documents"] += result.documents
            totals["indexed_nodes"] += result.indexed_nodes
            totals["skipped_documents"] += result.skipped_documents
        if options.prune_orphans:
            # prune 必须以全库有效集合为准，不能只使用 --conversation-id 的子集。
            async with async_session() as db:
                all_rows = list((await db.execute(
                    select(Conversation.user_id, Conversation.id)
                )).all())
            valid_pairs = {(str(user_id), str(conv_id)) for user_id, conv_id in all_rows}
            totals["pruned_orphans"] = await service.prune_orphans(valid_pairs)
        return totals
    finally:
        await service.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--conversation-id")
    parser.add_argument("--user-id")
    parser.add_argument("--prune-orphans", action="store_true")
    options = parser.parse_args()
    print(json.dumps(asyncio.run(run(options)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
