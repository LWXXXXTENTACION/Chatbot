"""
Supervisor Agent: iterative decision-maker for the multi-agent system.

Unlike the Router (one-time classification), the Supervisor:
  1. Analyzes the user request and conversation progress
  2. Decides which specialist agent to call next (or finish)
  3. Evaluates agent outputs before deciding the next step
  4. Can chain multiple agents for complex, multi-step tasks

This is a true Supervisor-Worker pattern:
  Supervisor → Specialist → Tool loop → Supervisor → Specialist/END
"""

import json
import logging
from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.config import DeepSeekModelId
from app.graph.state import AgentState

logger = logging.getLogger("chatbot.supervisor")

# Max supervisor iterations (safety limit — prevents infinite loops).
# Most tasks complete in 1-2 iterations. Higher values increase
# the risk of repetitive agent calls.
MAX_SUPERVISOR_ITERATIONS = 3

# Agent categories for the routing function.
AGENT_CATEGORIES = Literal["code", "math", "creative", "general"]


SUPERVISOR_SYSTEM_PROMPT = """你是一个任务监督者（Supervisor），负责协调多个专业 AI Agent 协作完成用户请求。

## 可用 Agent

| Agent | 专长 | 何时使用 |
|-------|------|---------|
| **code** | 编程、算法、架构 | 用户需要写代码、调试、技术方案 |
| **math** | 数学计算、数据分析 | 用户需要公式推导、统计计算 |
| **creative** | 写作、翻译、文案 | 用户需要文章、翻译、润色 |
| **general** | 通用知识问答 | 概念解释、闲聊、信息查询 |

## 核心原则（重要！）

1. **默认 finish**：调用一个 Agent 后，如果它的输出已经覆盖了用户的核心需求，立即返回 finish。不要为了"完整"而调用更多 Agent。
2. **不要重复**：绝不连续调用两个相同类型的 Agent。如果第一个 Agent 的输出不满意，返回 finish 让用户自己追问。
3. **任务分离**：如果确实需要两个 Agent（如"写代码 + 写文档"），给它们的 task 必须互不重叠：
   - code Agent 的 task: "仅编写代码和代码注释，不要写使用文档"
   - creative Agent 的 task: "根据已有代码，撰写面向用户的使用文档，不要重复贴代码"
4. **一个 Agent 能完成的就不要用两个**：大多数请求一个 Agent 即可完成。
5. **第 1 轮**：选择最合适的 Agent，给出完整的 task 描述
6. **第 2+ 轮**：只有在当前输出明显缺少用户需求的关键部分时，才调用另一个 Agent

## 输出格式

仅输出 JSON，不要任何额外文字：
{
  "action": "call_agent" | "finish",
  "agent": "code" | "math" | "creative" | "general",
  "task": "发给 Agent 的具体任务（互不重叠，明确边界）",
  "reason": "一句话决策理由"
}

## 当前状态

用户请求和对话历史见上方消息。根据当前进度，决定下一步行动。"""


# ——— Supervisor Node ———


async def supervisor_node(state: AgentState, config: RunnableConfig) -> dict:
    """Iterative decision-maker: analyze progress and decide next action.

    Called at the START of each turn and after every specialist response.
    Evaluates the conversation state and either dispatches another agent
    or signals completion.

    Returns:
        dict with supervisor_action, supervisor_target, supervisor_task,
        supervisor_iteration, and route_category (for backward compat).
    """
    from app.llm.client import create_deepseek_chat

    messages: list[BaseMessage] = state.get("messages", [])
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore
    iteration: int = state.get("supervisor_iteration", 0)
    base_system_prompt: str = state.get("system_prompt", "")

    # ——— Safety: force finish if max iterations exceeded ———
    if iteration >= MAX_SUPERVISOR_ITERATIONS:
        logger.warning(
            f"Supervisor hit max iterations ({MAX_SUPERVISOR_ITERATIONS}), forcing finish"
        )
        return {
            "supervisor_action": "finish",
            "supervisor_target": "general",
            "supervisor_task": "",
            "supervisor_iteration": iteration,
            "route_category": "general",
        }

    # ——— Build prompt ———
    effective_prompt = SUPERVISOR_SYSTEM_PROMPT
    if base_system_prompt.strip():
        effective_prompt = f"{SUPERVISOR_SYSTEM_PROMPT}\n\n用户额外指示：{base_system_prompt}"

    llm_input: list[BaseMessage] = [SystemMessage(content=effective_prompt)]
    llm_input.extend(list(messages))

    # ——— Add progress context ———
    if iteration == 1:
        # After first agent: default to finish unless there's a clear gap
        progress_msg = (
            "\n[系统提示] 第 1 个 Agent 已完成回复（见上方）。"
            "默认应该返回 finish 结束任务。"
            "只有在当前输出明显缺少用户需求的某个关键部分时，"
            "才调用第 2 个 Agent（且类型不能与已调用的相同）。"
            "如果输出已基本满足需求，直接 finish。"
        )
        llm_input.append(HumanMessage(content=progress_msg))
    elif iteration >= 2:
        # After second agent: strongly push to finish
        progress_msg = (
            f"\n[系统提示] 这是第 {iteration + 1} 轮决策，已调用了 {iteration} 个 Agent。"
            "除非当前输出有严重缺失，否则必须返回 finish。"
            "不要再调用更多 Agent。"
        )
        llm_input.append(HumanMessage(content=progress_msg))

    # ——— Call LLM for decision ———
    llm = create_deepseek_chat(model_id, temperature=0.1)

    try:
        response = await llm.ainvoke(llm_input)
        text = response.content if isinstance(response.content, str) else str(response.content)
        text = text.strip()

        # Handle markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        action = result.get("action", "finish")
        agent = result.get("agent", "general")
        task = result.get("task", "")
        reason = result.get("reason", "")

        # Validate
        if action not in ("call_agent", "finish"):
            action = "finish"
        if agent not in ("code", "math", "creative", "general"):
            agent = "general"

        logger.info(
            f"Supervisor [iter {iteration}]: {action} → {agent} | {reason}"
        )

        new_iteration = iteration + 1

        return {
            "supervisor_action": action,
            "supervisor_target": agent,
            "supervisor_task": task,
            "supervisor_iteration": new_iteration,
            "route_category": agent,  # backward compat for specialist_tool_node routing
        }

    except json.JSONDecodeError as e:
        logger.warning(f"Supervisor JSON parse failed, defaulting to general: {e}")
        return {
            "supervisor_action": "call_agent",
            "supervisor_target": "general",
            "supervisor_task": "请回答用户的问题",
            "supervisor_iteration": iteration + 1,
            "route_category": "general",
        }
    except Exception as e:
        logger.exception(f"Supervisor error, finishing: {e}")
        return {
            "supervisor_action": "finish",
            "supervisor_target": "general",
            "supervisor_task": "",
            "supervisor_iteration": iteration,
            "route_category": "general",
            "error": str(e),
        }


# ——— Conditional Edge ———


def supervisor_route(
    state: AgentState,
) -> Literal["code", "math", "creative", "general", "__end__"]:
    """Route based on supervisor's decision.

    - "call_agent" → route to the target specialist
    - "finish" → end the graph
    """
    action = state.get("supervisor_action", "call_agent")
    if action == "finish":
        logger.info("Supervisor: task complete → END")
        return "__end__"

    target = state.get("supervisor_target", "general")
    if target not in ("code", "math", "creative", "general"):
        target = "general"
    logger.info(f"Supervisor: dispatch → {target}")
    return target  # type: ignore[return-value]
