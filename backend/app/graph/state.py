"""Supervisor 与 Worker 子图共享的可序列化状态定义。

学习 LangGraph 时最重要的边界是：会参与流程判断、需要 checkpoint 的数据放
State；缓存客户端、并发锁等运行依赖放 Runtime。不要把两者混在一起。
"""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.config import DeepSeekModelId


class SourceCitation(TypedDict):
    """搜索来源的统一结构，既能 checkpoint，也能随最终消息持久化。"""

    title: str
    url: str
    content: str
    score: NotRequired[Any]


AgentName = Literal["supervisor", "general_agent", "research_agent"]
WorkerRoute = Literal["general_agent", "research_agent"]
GeneralTaskRoute = Literal["standard", "artifact"]
ResearchToolName = Literal["web_search", "deep_search"]
ContextStrategy = Literal[
    "microcompact",
    "context_collapse",
    "session_memory",
    "full_compact",
    "ptl_truncation",
]


class SupervisorDecision(TypedDict):
    """Supervisor 的可审计分派；条件边只读取 route，不重新猜任务。"""

    route: WorkerRoute
    task: str
    reason: str


class ArtifactPlan(TypedDict):
    """Artifact 子流程使用的可序列化生成计划。"""

    kind: Literal["code", "html", "markdown", "svg"]
    language: str | None
    title: str


class ResearchPlan(TypedDict):
    """Research 子流程使用的确定性工具计划。"""

    tool_name: ResearchToolName
    query: str
    focus: str
    max_results: int | None


class ContextReport(TypedDict):
    """一次上下文治理的可观测报告，用于前端时间线和 Eval。"""

    strategies: list[ContextStrategy]
    estimated_tokens_before: int
    estimated_tokens_after: int
    max_tokens: int
    pressure_before: float
    pressure_after: float
    compacted_tool_results: int
    removed_messages: int
    overflowed: bool


class AgentInput(TypedDict):
    """API 启动一轮 Graph 时提供的最小输入。

    ``messages`` 是唯一带 reducer 的输入字段。checkpoint 与业务历史同步时，
    API 只需要提交新的 HumanMessage，``add_messages`` 会把它合并进 thread 历史。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    model_id: DeepSeekModelId
    system_prompt: str
    user_id: str
    conversation_id: str


class AgentState(AgentInput):
    """父图和两个 Worker 子图共同读写的完整状态。

    分派、Worker 结果和子图进度都是 turn-local。thread 恢复后必须先经过
    ``prepare_turn``，否则上一轮的 route 或 artifact_plan 会污染新请求。
    """

    supervisor_decision: SupervisorDecision | None
    active_agent: AgentName | None
    completed_agents: list[AgentName]
    worker_result: str
    source_citations: list[SourceCitation]
    context_summary: str
    session_memory: str
    session_memory_cursor: str
    context_report: ContextReport | None

    # 子图进度属于当前回合，必须由 prepare_turn 在每次运行前清空。
    general_task_route: GeneralTaskRoute | None
    tool_rounds: int
    artifact_plan: ArtifactPlan | None
    artifact_content: str
    research_plan: ResearchPlan | None
    error: str | None


class AgentOutput(TypedDict):
    """API 持久化层只关心最终消息与错误，不暴露内部协调字段。"""

    messages: Annotated[list[BaseMessage], add_messages]
    error: str | None
