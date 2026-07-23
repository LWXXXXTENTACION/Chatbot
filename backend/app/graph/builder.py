"""组装 Supervisor 父图，并直接挂载两个编译后的 Worker 子图。"""

from langgraph.graph import END, START, StateGraph

from app.agents.general import GENERAL_AGENT_GRAPH
from app.agents.research import RESEARCH_AGENT_GRAPH
from app.agents.supervisor import supervisor_finalize_node, supervisor_node
from app.graph.context import AgentRuntimeContext
from app.graph.context_manager import context_manager_node
from app.graph.context_index_nodes import archive_context_node, retrieve_context_node
from app.graph.nodes import prepare_turn_node
from app.graph.routing import route_supervisor
from app.graph.state import AgentInput, AgentOutput, AgentState


def build_graph(*, checkpointer=None):
    """编译一次严格的 Supervisor 分派与整合工作流。

    START → prepare_turn → retrieve_context → context_manager → archive_context →
    supervisor → one worker →
    supervisor_finalize → END

    Worker 使用编译子图直接作为节点。共享 ``AgentState`` 由父图唯一
    checkpointer 持久化，Runtime 则只向具体节点注入缓存和工具预算。
    """
    graph = StateGraph(
        AgentState,
        context_schema=AgentRuntimeContext,
        input_schema=AgentInput,
        output_schema=AgentOutput,
    )
    graph.add_node("prepare_turn", prepare_turn_node)
    graph.add_node("retrieve_context", retrieve_context_node)
    graph.add_node("context_manager", context_manager_node)
    graph.add_node("archive_context", archive_context_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("general_agent", GENERAL_AGENT_GRAPH)
    graph.add_node("research_agent", RESEARCH_AGENT_GRAPH)
    graph.add_node("supervisor_finalize", supervisor_finalize_node)

    graph.add_edge(START, "prepare_turn")
    graph.add_edge("prepare_turn", "retrieve_context")
    graph.add_edge("retrieve_context", "context_manager")
    graph.add_edge("context_manager", "archive_context")
    graph.add_edge("archive_context", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "general_agent": "general_agent",
            "research_agent": "research_agent",
        },
    )
    graph.add_edge("general_agent", "supervisor_finalize")
    graph.add_edge("research_agent", "supervisor_finalize")
    graph.add_edge("supervisor_finalize", END)
    return graph.compile(checkpointer=checkpointer, name="supervisor_chat_workflow")
