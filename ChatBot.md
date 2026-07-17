## DeepSeek Agent Workspace

**角色：全栈开发 / AI Agent 工程**

### 项目背景

面向产品调研、技术研究、计算分析和轻量原型等知识任务，独立开发可执行工具、生成交付物并持续评测优化的 AI Agent 工作台，而非仅提供问答的模型套壳。

### 核心难点

1. 多 Agent 分工、工具调用与最终回复需要保持单一可审计执行链。
2. 长会话需保留事实与约束，同时控制 Token 和工具调用膨胀。
3. 模型生成的 HTML/SVG 与工具结果需要安全展示、限额和回放。

### 解决方案

1. 通过 LangGraph Supervisor 编排 General/Research Agent，实现研究、计算、搜索与 Artifact 任务的职责隔离。
2. 通过五层上下文治理及 ToolPolicy/Registry，实现每批 **3 次**、每回合 **6 次**、并发 **3 次**的资源边界。
3. 通过 CSP 沙箱、结构化 ToolOutcome 和独立 Eval Lab，实现工件安全预览及 Token、耗时、调用和质量的版本对比。

### 项目亮点

- 支持带引用 Deep Search，以及代码、HTML、SVG、Markdown 可预览交付物。
- 工具结果上限由 **20,046 字符降至 4,000 字符**，后端 **48 项测试通过**。
- 运行 Trace 与业务消息同步持久化，可按固定 Case 回放每轮 Agent 优化效果。
