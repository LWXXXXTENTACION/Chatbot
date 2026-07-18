# LangGraph 全栈教程 Chatbot

### 项目背景

面向刚学习 LangGraph、希望掌握全栈 AI 开发的开发者，构建覆盖 Multi-Agent、Artifact、Context/Memory Engineering、多层缓存、可靠 SSE 与 Evals 的开源教程 Chatbot，并以中文注释和显式 Graph 拓扑降低学习成本。

### 核心难点

1. Multi-Agent、长会话上下文和记忆提取需要形成单一、可审计的数据流。
2. SSE 需同时处理逐 token 卡顿、断流丢失、TCP 拆包乱码与刷新恢复。
3. Redis、业务库、Graph checkpoint、SSE 日志和浏览器草稿需要职责清晰且可降级。

### 解决方案

1. 通过共享 AgentState、编译子图和显式 Node/Edge，实现任务分派、工具循环、研究及 Artifact DAG。
2. 通过五层 Context Engineering、summary/memory 分离和 cursor 增量提取，实现长会话成本治理与 thread 级记忆。
3. 通过双指针+rAF、Last-Event-ID 手动续传、Buffer 分帧和 localStorage 增量草稿，实现稳定 SSE；通过 Redis fail-open 缓存与双 SQLite 分工实现分层恢复。

### 项目亮点

- 支持带引用 Deep Search，以及 HTML、SVG、Markdown、代码和 PDF 打印预览 Artifact。
- 受 Claude Code 启发实现五层上下文治理，并建立浏览器、SSE Journal、Redis、业务库和 checkpoint 多层状态体系。
- 30,000 个 delta 的 UI 发布减少 **98.44%**，当前 **62 项后端测试通过**。
- 内置独立 Eval Lab，可按版本对比 Token、耗时、工具调用和回答质量。
