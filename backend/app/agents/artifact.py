"""Artifact 专用节点：计划、生成、构造工具调用、汇总执行结果。"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import uuid
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import StreamWriter

from app.config import tools_enabled
from app.graph.events import emit_tool_call
from app.graph.model import stream_model_message
from app.graph.state import AgentState, ArtifactPlan
from app.tools.artifact import MAX_ARTIFACT_CONTENT_CHARS

logger = logging.getLogger("chatbot.agents.artifact")

_ARTIFACT_ACTIONS = ("创建", "生成", "导出", "制作", "预览")
_ARTIFACT_TARGETS = (
    "artifact", "工件", "pdf", "html", "网页", "页面", "文档", "svg", "代码文件",
)
_ARTIFACT_NEGATIONS = ("不要创建", "不用创建", "无需创建", "直接在聊天", "不要生成文件")

ARTIFACT_GENERATION_PROMPT = """你正在执行专用文档工件工作流。

目标工件类型：{kind}
用户任务：{task}

只输出工件的完整正文，不要解释，不要使用 Markdown 代码围栏，也不要声称无法创建文件。
- html：输出完整的 <!DOCTYPE html> 文档；使用语义化结构、清晰排版和内联 CSS。
- PDF 预览：仍输出完整 HTML，并包含适合 A4 打印的 @page 与 print 样式。
- markdown：输出完整 Markdown 文档。
- svg：输出单个完整 <svg> 元素。
- code：只输出完整代码文件正文。
充分使用当前对话中已有内容，不要只输出提纲或占位文字。
"""

_FENCED_CONTENT_RE = re.compile(
    r"```(?:html|markdown|md|svg|[\w+-]+)?\s*\n?([\s\S]*?)\n?```",
    re.IGNORECASE,
)


def artifact_required(task: str) -> bool:
    """根据 Supervisor 的任务文本选择 Artifact 显式分支。"""
    normalized = task.casefold()
    if any(negation in normalized for negation in _ARTIFACT_NEGATIONS):
        return False
    return (
        any(action in normalized for action in _ARTIFACT_ACTIONS)
        and any(target in normalized for target in _ARTIFACT_TARGETS)
    )


def artifact_spec(task: str) -> ArtifactPlan:
    """把自然语言目标转换成稳定、可 checkpoint 的工件计划。"""
    normalized = task.casefold()
    if "svg" in normalized:
        return {"kind": "svg", "language": "xml", "title": "SVG 图形"}
    if "pdf" in normalized:
        return {"kind": "html", "language": "html", "title": "PDF 文档预览"}
    if any(target in normalized for target in ("html", "网页", "页面")):
        return {"kind": "html", "language": "html", "title": "网页工件"}
    if any(target in normalized for target in ("markdown", "文档")):
        return {"kind": "markdown", "language": "markdown", "title": "文档工件"}
    return {"kind": "code", "language": None, "title": "代码工件"}


def _strip_fence(value: str) -> str:
    matches = _FENCED_CONTENT_RE.findall(value)
    return max(matches, key=len).strip() if matches else value.strip()


def normalize_artifact_content(content: str, kind: str) -> str:
    """清洗模型输出，保证侧边栏收到完整且有界的正文。"""
    normalized = _strip_fence(content)
    if not normalized:
        raise ValueError("文档模型未返回可用的工件内容")

    if kind == "html":
        lowered = normalized.casefold()
        start = lowered.find("<!doctype")
        if start < 0:
            start = lowered.find("<html")
        if start >= 0:
            normalized = normalized[start:]
            if not normalized.casefold().startswith("<!doctype"):
                normalized = f"<!DOCTYPE html>\n{normalized}"
        else:
            has_html_fragment = bool(re.search(
                r"<(?:main|section|article|div|h[1-6]|p|table)\b",
                normalized,
                re.IGNORECASE,
            ))
            body = normalized if has_html_fragment else (
                '<pre style="white-space:pre-wrap">'
                f"{html.escape(normalized)}"
                "</pre>"
            )
            normalized = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>@page{{size:A4;margin:16mm}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#171717;line-height:1.65}}main{{max-width:780px;margin:0 auto;padding:32px}}@media print{{main{{padding:0}}}}</style>
</head><body><main>{body}</main></body></html>"""
    elif kind == "svg":
        start = normalized.casefold().find("<svg")
        end = normalized.casefold().rfind("</svg>")
        if start >= 0 and end >= start:
            normalized = normalized[start:end + len("</svg>")]

    return normalized[:MAX_ARTIFACT_CONTENT_CHARS]


def prepare_artifact_node(state: AgentState) -> dict[str, Any]:
    """节点 1：从 Supervisor 任务创建 Artifact 计划。"""
    decision = state.get("supervisor_decision")
    if not decision:
        return {"error": "Supervisor 未提供 Artifact 任务"}
    return {"artifact_plan": artifact_spec(decision["task"])}


async def generate_artifact_node(
    state: AgentState,
    writer: StreamWriter,
) -> dict[str, Any]:
    """节点 2：模型只生成正文，不在这里伪装或执行工具。"""
    plan = state.get("artifact_plan")
    decision = state.get("supervisor_decision")
    if not plan or not decision:
        return {"error": "Artifact 生成节点缺少计划或任务"}
    try:
        generated = await stream_model_message(
            state,
            writer=writer,
            system_prompts=[ARTIFACT_GENERATION_PROMPT.format(
                kind=plan["kind"],
                task=decision["task"],
            )],
            tools=None,
            attach_sources=False,
            emit_text=False,
            emit_reasoning=False,
            strip_tool_protocol=not tools_enabled(state.get("model_id", "deepseek-v4-flash")),
        )
        return {
            "artifact_content": normalize_artifact_content(
                str(generated.content or ""),
                plan["kind"],
            )
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Artifact content generation failed")
        return {"error": str(exc)}


async def build_artifact_call_node(
    state: AgentState,
    writer: StreamWriter,
) -> dict[str, Any]:
    """节点 3：把正文转换为标准 AIMessage.tool_calls 协议。"""
    plan = state.get("artifact_plan")
    content = state.get("artifact_content", "")
    if state.get("error"):
        return {}
    if not plan or not content:
        return {"error": "Artifact 工具调用节点缺少计划或正文"}

    args: dict[str, Any] = {
        "title": plan["title"],
        "kind": plan["kind"],
        "content": content,
    }
    if plan["language"]:
        args["language"] = plan["language"]
    message = AIMessage(
        content="",
        id=uuid.uuid4().hex,
        tool_calls=[{
            "id": f"artifact_{uuid.uuid4().hex}",
            "name": "create_artifact",
            "args": args,
            "type": "tool_call",
        }],
    )
    await emit_tool_call(writer, message)
    # 正文已经进入标准 tool_call，清空临时副本，避免最终 checkpoint 保存两份大内容。
    return {"messages": [message], "artifact_content": ""}


def finalize_artifact_node(state: AgentState) -> dict[str, Any]:
    """节点 5：只根据 ToolMessage 判断创建结果，不再次调用模型。"""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, ToolMessage) or last.name != "create_artifact":
        return {"worker_result": "文档工件创建失败：未得到有效工具结果。"}
    try:
        result = json.loads(str(last.content))
    except (json.JSONDecodeError, TypeError):
        result = {}
    if last.status == "success" and isinstance(result, dict) and result.get("ok"):
        return {"worker_result": "文档工件已创建并已在侧边栏打开。"}
    error = result.get("error") if isinstance(result, dict) else None
    return {"worker_result": f"文档工件创建失败：{error or '工具执行未成功'}。"}
