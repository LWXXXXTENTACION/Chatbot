"""
RouterAgent: analyzes user intent and routes to specialized agents.

Uses a lightweight LLM call with structured output to classify the
user's last message into one of four categories:
  - code:     Programming, algorithms, debugging, architecture
  - math:     Calculation, formula derivation, data analysis
  - creative: Writing, translation, polishing, creative content
  - general:  General knowledge, chitchat, everything else
"""

import json
import logging
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.config import DeepSeekModelId
from app.graph.state import AgentState

logger = logging.getLogger("chatbot.router")

# ——— Structured output schema ———

ROUTE_CATEGORIES = Literal["code", "math", "creative", "general"]

ROUTER_SYSTEM_PROMPT = """你是一个请求分类器。分析用户意图，将请求归类到以下 4 个类别之一：

- **code**: 编程、代码、算法、调试、架构设计、技术选型
- **math**: 数学计算、公式推导、数据分析、统计、物理化学计算
- **creative**: 写作、翻译、润色、创意文案、故事、诗歌、邮件
- **general**: 通用知识问答、闲聊、概念解释、其他所有

仅输出 JSON，不要添加任何其他文字：
{"category": "选择的类别", "reason": "一句话说明分类理由"}"""


# ——— Router node ———


async def router_node(state: AgentState, config: RunnableConfig) -> dict:
    """Analyze the user's last message and classify intent.

    Uses a quick LLM call to decide which specialist agent should
    handle the request. Stores the decision in route_category.
    """
    from app.llm.client import create_deepseek_chat

    messages: list[BaseMessage] = state.get("messages", [])
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore

    # Find the last user message
    user_text = ""
    for msg in reversed(list(messages)):
        if isinstance(msg, HumanMessage):
            content = msg.content
            if isinstance(content, str):
                user_text = content
            elif isinstance(content, list):
                text_bits = [
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                user_text = "".join(text_bits)
            break

    if not user_text:
        return {"route_category": "general"}

    # Quick classification via LLM
    llm = create_deepseek_chat(model_id, temperature=0.1)

    try:
        response = await llm.ainvoke([
            SystemMessage(content=ROUTER_SYSTEM_PROMPT),
            HumanMessage(content=f"用户消息：{user_text[:2000]}"),
        ])
        text = response.content if isinstance(response.content, str) else str(response.content)
        # Parse JSON from response
        text = text.strip()
        # Handle markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]

        result = json.loads(text)
        category = result.get("category", "general")
        reason = result.get("reason", "")

        # Validate category
        if category not in ("code", "math", "creative", "general"):
            category = "general"

        logger.info(f"Router: [{category}] {reason}")

        return {"route_category": category}

    except Exception as e:
        logger.warning(f"Router classification failed, falling back to general: {e}")
        return {"route_category": "general"}


# ——— Conditional edge ———


def route_condition(state: AgentState) -> Literal["code", "math", "creative", "general"]:
    """Conditional routing: maps route_category to the correct specialist node."""
    category = state.get("route_category", "general")
    if category not in ("code", "math", "creative", "general"):
        return "general"
    return category  # type: ignore[return-value]
