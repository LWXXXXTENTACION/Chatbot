# LangGraph 工作流结构

本目录遵循一个原则：**控制流写在 Graph 的 node/edge 中，纯数据处理保留为普通函数。**
这样既能从拓扑读懂业务流程，也不会把 JSON 解析、HTML 清洗等无状态细节拆成没有意义的节点。

## 父图

```text
START
  → prepare_turn
  → context_manager
  → supervisor
      ├→ general_agent（编译子图）
      └→ research_agent（编译子图）
  → supervisor_finalize
  → END
```

- `prepare_turn`：统一清空上轮的临时字段。
- `context_manager`：根据上下文压力生成一次原子、reducer-safe 的状态更新。
- `supervisor`：只负责形成可审计的 Worker 分派。
- Worker 节点直接挂载编译子图，不再在普通节点里手动 `astream` 并转发结果。
- `supervisor_finalize`：唯一面向用户输出正文的模型节点。

## General 子图

```text
prepare_general
  ├→ general_model → general_tools → general_model（最多 3 轮）
  │                    └→ general_tool_limit
  └→ prepare_artifact
       → generate_artifact
       → build_artifact_call
       → artifact_tools
       → finalize_artifact
  → complete_general
  → END
```

Artifact 不再藏在模型节点的条件语句中。模型只生成正文，后续节点负责构造标准
`AIMessage.tool_calls`、执行 `create_artifact`、读取 `ToolMessage` 并完成 Worker。

## Research 子图

```text
prepare_research
  → build_research_call
  → research_tools
  → finalize_research
  → END
```

Deep Search 本身仍是独立的 `plan_queries → search_sources → synthesize_brief` 子图。
它由 Research 工具节点调用，嵌套事件由 LangGraph 的 `subgraphs=True` 自动传播。

## State、Runtime 与事件边界

- `AgentState` 只保存可序列化、需要 checkpoint 的业务数据和消息协议。
- `Runtime[AgentRuntimeContext]` 只注入缓存、搜索模式、工具额度等请求级依赖，不决定流程跳转。
- 父图拥有持久化 checkpointer；Worker 子图使用默认继承作用域，不创建独立 saver。
- Deep Search 是无状态工具子图，明确使用 `checkpointer=False`。
- 所有自定义 SSE 事件从 `events.py` 发送；API 只转发 `custom` 事件，并只把根命名空间的 `values` 当作最终状态。
