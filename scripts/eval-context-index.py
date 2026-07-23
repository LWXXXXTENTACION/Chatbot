#!/usr/bin/env python3
"""用 LlamaIndex RetrieverEvaluator 评测中英文长对话语义召回。"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    node_id: str
    user_id: str
    conversation_id: str
    archive_text: str
    summary_memory: str
    query: str
    answer_fact: str


CASES = [
    EvalCase("zh-trip", "u-zh", "c-trip", "用户三个月前确定京都住宿叫樱庭旅舍，位于四条河原町，且只吃素食。", "用户计划去京都。", "我在京都订的旅舍叫什么？", "樱庭旅舍"),
    EvalCase("zh-project", "u-zh", "c-project", "项目代号是青岚，持久层采用 SQLite，发布窗口定在周四晚八点。", "项目正在开发。", "青岚项目安排在什么时候发布？", "周四晚八点"),
    EvalCase("en-pref", "u-en", "c-pref", "The user named the dashboard Northstar and requested high contrast charts with no gradients.", "A dashboard is being designed.", "What visual constraint did I set for Northstar?", "no gradients"),
    EvalCase("en-api", "u-en", "c-api", "The migration uses the codename Cedar. The required compatibility target is Python 3.13.", "There is an API migration.", "Which Python version must Cedar support?", "Python 3.13"),
    EvalCase("zh-doc", "u-doc", "c-doc", "Artifact 标题为《供应链风险手册》，交付类型是 A4 PDF 预览，审阅人为李明。", "创建过一个文档。", "供应链手册由谁审阅？", "李明"),
    EvalCase("en-budget", "u-budget", "c-budget", "The approved inference budget is 42 million tokens per month, with alerts at 80 percent.", "A budget was approved.", "At what percentage should inference budget alerts fire?", "80 percent"),
]


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * ratio)]


async def run() -> dict[str, Any]:
    from llama_index.core import VectorStoreIndex
    from llama_index.core.evaluation import RetrieverEvaluator
    from llama_index.core.schema import TextNode
    from llama_index.core.vector_stores import (
        FilterOperator,
        MetadataFilter,
        MetadataFilters,
    )
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding

    embed_model = HuggingFaceEmbedding(
        model_name="BAAI/bge-small-zh-v1.5",
        trust_remote_code=False,
    )
    nodes = [TextNode(
        id_=case.node_id,
        text=case.archive_text,
        metadata={
            "user_id": case.user_id,
            "conversation_id": case.conversation_id,
        },
    ) for case in CASES]
    index = VectorStoreIndex(nodes, embed_model=embed_model)

    hit_rates: list[float] = []
    mrrs: list[float] = []
    recalls: list[float] = []
    latencies: list[float] = []
    enhanced_fact_hits = 0
    baseline_fact_hits = 0
    leakage = 0
    details: list[dict[str, Any]] = []

    for case in CASES:
        filters = MetadataFilters(filters=[
            MetadataFilter(key="user_id", value=case.user_id, operator=FilterOperator.EQ),
            MetadataFilter(
                key="conversation_id",
                value=case.conversation_id,
                operator=FilterOperator.EQ,
            ),
        ])
        retriever = index.as_retriever(similarity_top_k=4, filters=filters)
        evaluator = RetrieverEvaluator.from_metric_names(
            ["hit_rate", "mrr"], retriever=retriever
        )
        evaluation = await evaluator.aevaluate(
            query=case.query,
            expected_ids=[case.node_id],
        )
        metrics = evaluation.metric_vals_dict
        retrieved = await retriever.aretrieve(case.query)
        retrieved_ids = [item.node.node_id for item in retrieved]
        retrieved_text = "\n".join(item.node.get_content() for item in retrieved)
        hit = len({case.node_id}.intersection(retrieved_ids))
        recall = hit
        hit_rates.append(float(metrics.get("hit_rate", 0.0)))
        mrrs.append(float(metrics.get("mrr", 0.0)))
        recalls.append(float(recall))
        enhanced_fact_hits += int(case.answer_fact.casefold() in retrieved_text.casefold())
        baseline_fact_hits += int(case.answer_fact.casefold() in case.summary_memory.casefold())
        leakage += sum(
            1 for item in retrieved
            if item.node.metadata.get("user_id") != case.user_id
            or item.node.metadata.get("conversation_id") != case.conversation_id
        )
        for _ in range(8):
            started = time.perf_counter()
            await retriever.aretrieve(case.query)
            latencies.append((time.perf_counter() - started) * 1000)
        details.append({
            "case": case.node_id,
            "hit_rate": metrics.get("hit_rate", 0.0),
            "mrr": metrics.get("mrr", 0.0),
            "recall_at_4": recall,
            "retrieved_ids": retrieved_ids,
        })

    result = {
        "dataset": "6 manually annotated Chinese/English long-conversation facts",
        "evaluator": "LlamaIndex RetrieverEvaluator",
        "hit_rate_at_4": round(statistics.fmean(hit_rates), 4),
        "mrr_at_4": round(statistics.fmean(mrrs), 4),
        "recall_at_4": round(statistics.fmean(recalls), 4),
        "retrieval_p50_ms": round(statistics.median(latencies), 3),
        "retrieval_p95_ms": round(percentile(latencies, 0.95), 3),
        "cross_tenant_leakage": leakage,
        "baseline_summary_memory_fact_hit_rate": round(
            baseline_fact_hits / len(CASES), 4
        ),
        "enhanced_fact_hit_rate": round(enhanced_fact_hits / len(CASES), 4),
        "details": details,
    }
    result["passed"] = bool(
        result["hit_rate_at_4"] >= 0.85
        and result["mrr_at_4"] >= 0.75
        and result["cross_tenant_leakage"] == 0
        and result["retrieval_p50_ms"] <= 150
        and result["retrieval_p95_ms"] <= 300
        and result["enhanced_fact_hit_rate"]
        > result["baseline_summary_memory_fact_hit_rate"]
    )
    return result


def main() -> None:
    try:
        result = asyncio.run(run())
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "缺少 LlamaIndex 运行依赖；请先按 backend/pyproject.toml 同步后端环境："
            f" {exc}"
        ) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
