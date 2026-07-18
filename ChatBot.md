# LangGraph 全栈教程 Chatbot

### 项目背景

面向刚学习 LangGraph、希望掌握全栈 AI 开发的开发者，构建覆盖 Multi-Agent、工具、Artifact、SSE、持久化与 Evals 的开源教程 Chatbot，并以中文注释和显式 Graph 拓扑降低学习成本。

### 核心难点

1. Supervisor、General、Research 与 Artifact 需要形成单一、可审计的工作流。
2. SSE 需同时处理 TCP 拆包、Unicode、逐 token 重渲染、断流续传和重复订阅。
3. 业务消息、LangGraph checkpoint、工具协议和侧栏 Artifact 需要保持一致。

### 解决方案

1. 通过共享 AgentState、编译子图和显式 Node/Edge，实现任务分派、工具循环、研究及 Artifact DAG。
2. 通过增量 Parser、Last-Event-ID、事件日志与 rAF 双缓冲，实现无损续传和低频 UI 发布。
3. 通过 SQLAlchemy、AsyncSqliteSaver、ToolPolicy 与标准 AIMessage/ToolMessage，实现持久化、安全边界和可恢复执行。

### 项目亮点

- 支持带引用 Deep Search，以及 HTML、SVG、Markdown、代码和 PDF 打印预览 Artifact。
- 30,000 个 delta 的 UI 发布减少 **98.44%**，当前 **62 项后端测试通过**。
- 内置独立 Eval Lab，可按版本对比 Token、耗时、工具调用和回答质量。
