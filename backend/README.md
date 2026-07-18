# LangGraph 全栈教程 Chatbot · Python 后端

后端由 FastAPI、LangGraph、SQLAlchemy Async 和 POST SSE 组成。本文从“代码应该从哪里开始读”的角度解释模块边界；完整产品介绍与前端数据流见根目录 [README](../README.md)。

## 快速启动

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp ../.env.example .env
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

也可以在项目根目录同时启动前后端：

```bash
npm run dev:all
```

Redis 是可选依赖。未启用时，工具缓存退化为 miss，限流退化为当前进程内实现；聊天、LangGraph 和 SQLite 不受影响。

## 代码入口

| 文件 | 职责 |
|---|---|
| `app/main.py` | 创建 FastAPI 生命周期资源：迁移、checkpointer、Graph、Redis、SSE Registry |
| `app/routers/chat.py` | 鉴权后的聊天入口、业务历史同步、Graph 运行和消息持久化 |
| `app/graph/builder.py` | 只组装 Supervisor 父图 |
| `app/graph/state.py` | Graph 输入、共享 State、输出和 TypedDict |
| `app/agents/general.py` | General 子图和有界工具循环 |
| `app/agents/artifact.py` | Artifact 计划、生成、tool-call 构造和结果汇总节点 |
| `app/agents/research.py` | Research 子图 |
| `app/graph/deep_search.py` | 无状态 Deep Search DAG |
| `app/graph/tool_execution.py` | 工具白名单、Schema、额度、缓存、并发、超时和输出裁剪 |
| `app/graph/events.py` | 后端 SSE 事件类型和唯一发送入口 |
| `app/streaming.py` | 可续传 SSE 事件日志和订阅管理 |

## 严格 LangGraph 工作流

父图只做一轮任务的准备、分派和整合：

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

Supervisor 返回可审计的：

```json
{"route":"general_agent|research_agent","task":"完整任务","reason":"分派理由"}
```

显式 `web/deep` 模式直接选择 Research；自动模式使用 Supervisor 模型，JSON 无法解析时再走确定性关键词回退。只有 `supervisor_finalize` 会向用户流式输出最终正文，Worker 中间文本不会形成多个互相竞争的回答。

### General 子图

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

普通分支让支持 function calling 的模型选择天气、计算等工具。每次工具执行后检查轮数，因此达到上限时不会再产生一个缺少对应 `ToolMessage` 的新调用。

Artifact 分支是确定性 DAG：模型只生成正文，Graph 再构造标准 `AIMessage.tool_calls`，工具节点执行 `create_artifact`，最后从 `ToolMessage` 判断成功或失败。这样 DeepSeek Reasoner 即使不能绑定工具，也不会因强制 `tool_choice` 报错。

### Research 子图

```text
prepare_research
  → build_research_call
  → research_tools
  → finalize_research
  → END
```

Research 独占 `web_search` 和 `deep_search`。Web Search 执行一次快速查询；Deep Search 调用独立子图：

```text
START → plan_queries → search_sources → synthesize_brief → END
              1-3 个       并行检索、最多 8 个来源
```

Deep Search 是无状态工具工作流，因此明确使用 `checkpointer=False`。General/Research 则使用默认子图作用域，继承父图当前 invocation 的 checkpoint，不创建独立 saver。

## State、Runtime 与 Checkpointer

`AgentInput` 是 API 传入 Graph 的最小输入：

```text
messages / model_id / system_prompt / user_id / conversation_id
```

`AgentState` 再增加三类数据：

```text
分派状态：supervisor_decision / active_agent / completed_agents / worker_result
上下文状态：context_summary / session_memory / context_report / source_citations
子图状态：general_task_route / tool_rounds / artifact_plan / research_plan
```

关键边界：

- `messages` 使用 `add_messages` reducer，保证同 ID 更新和 AI/Tool 协议合并。
- 其他字段使用替换语义，每轮由 `prepare_turn` 清空 turn-local 字段。
- `Runtime[AgentRuntimeContext]` 只放缓存、搜索模式、工具额度等不可 checkpoint 的请求级依赖。
- 父图使用 `AsyncSqliteSaver`，`conversation_id` 就是 LangGraph `thread_id`。
- `chatbot.db` 保存用户真正看到的消息；`checkpoints.db` 保存 Agent 工作状态。

当业务历史最后一条消息与 checkpoint 不一致时，API 删除该 thread 的旧 checkpoint，并用业务历史重建。业务库始终是可见消息的事实来源。

## 上下文治理

`context_manager` 在 Supervisor 之前按顺序应用五种策略：

| 策略 | 默认触发 | 作用 |
|---|---:|---|
| `microcompact` | 工具结果超过 30 分钟 | 保留 ToolMessage ID 和协议，只缩小旧 payload |
| `session_memory` | 45% | 提取用户偏好、项目事实、约束和待办 |
| `context_collapse` | 62% | 压缩最早的一部分完整 turn |
| `full_compact` | 82% | 压缩所有可处理旧历史，保留最近 turn |
| `ptl_truncation` | 95% | 最后保护：按完整 turn 删除最早历史 |

所有删除都以完整 user turn 为单位，不会把 `AIMessage.tool_calls` 与对应 `ToolMessage` 拆开。摘要模型失败时使用有界本地 fallback，不让压缩失败中断聊天。

## 工具执行策略链

```text
Worker 白名单
  → Pydantic Schema
  → 每批 / 每回合额度
  → 用户确认策略
  → 精确缓存
  → 并发安全调度
  → timeout / cancellation
  → 模型输出与 UI 输出分别裁剪
  → ToolMessage + State Patch + tool_result 事件
```

默认边界：

- 每批最多 3 个工具调用。
- 每回合最多 6 个工具调用。
- 最大并发 3。
- `deep_search`、`create_artifact` 每回合最多一次。
- 同一批最多一个工具修改 Graph State。
- Artifact 正文最多 100,000 字符。

## Graph 到浏览器的数据流

```text
FastAPI 加载业务历史并保存 HumanMessage
  → graph.astream(..., stream_mode=["values", "custom"], subgraphs=True)
  → 子图 custom 事件由 LangGraph 自动传播
  → API 把 custom 写入 ResumableSSEStream
  → 只把根命名空间 values 当作最终 AgentState
  → 完整 AIMessage / ToolMessage 保存到 chatbot.db
  → trace_summary + done 结束流
```

使用 `subgraphs=True` 后会同时收到父图和子图的 `values`。`chat.py` 必须检查 `ns`，否则可能把子图中间快照误当成最终状态。

## 可续传 SSE

`ResumableSSEStream` 为每个 `stream_id` 维护：

- 严格递增的 event ID。
- 有上限的内存事件日志。
- 一个与 HTTP 订阅者解耦的 Graph producer task。
- 终止标记和完成时间。

断线后客户端携带相同 `stream_id` 与 `Last-Event-ID`。服务端只回放游标之后的事件，不重新运行 Graph。事件窗口已经过期时返回 `STREAM_REPLAY_EXPIRED`；用户主动停止才取消 producer。

Graph 节点事件定义在 `app/graph/events.py`：

```text
text_start / text_delta / text_end
reasoning_start / reasoning_delta / reasoning_end
tool_call_start / tool_call_delta / tool_call_end
tool_result / sources / activity / context_status
```

API 层另外发送 `trace_summary` 以及终止事件 `done/error`。字段命名与前端 `src/lib/types.ts` 保持一致。

## 测试

```bash
cd backend
.venv/bin/python -m pytest -q
```

当前测试覆盖 Graph 拓扑、子图 xray、Supervisor 路由、Artifact、工具协议、上下文治理、SQLite checkpoint、租户存储、SSE 续传与可观测性。项目根目录还可以运行：

```bash
npm run eval:sse
npm run build
```
