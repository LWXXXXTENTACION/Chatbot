## DeepSeek Chatbot

**项目背景**
基于 Next.js 16 + React 19 + AI SDK v6 构建的 AI 聊天助手，支持多模型切换、流式对话与 Tool Calling，全栈独立开发。

**核心难点**
1. AI SDK v6 的 Part 级流式消息渲染，text/reasoning/tool-* 四种 part 各有独立状态机，需在流式传输中保证 UI 平滑更新不闪烁
2. createArtifact 工具在流式传输中逐步到达内容，需要 ChatBubble 卡片与侧边栏 ArtifactPanel 实时同步，同时处理首次自动打开与后续增量更新间的时序协调
3. Reasoner 模型的 reasoning part 需在 UI 层单独渲染为可折叠思考块，并通过 shimmer 动画标识流式思考状态

**解决方案**
1. 通过 `InferUITools` 从服务端 tool 定义正向推导客户端消息类型，实现 PartView 分发器对四种 part 穷举渲染，TypeScript 编译器保证无遗漏分支
2. 通过 Zustand 的 `openArtifact` / `updateArtifact` 分离首次打开与增量更新逻辑，结合 `useRef` 标记 + `useEffect` 驱动内容同步，避免流式更新时重复打开面板
3. 通过 Markdown 组件 `memo` 优化，仅当 children 实际变化时才重渲染，降低流式场景下的重复解析开销

**项目亮点**
- 端到端流式渲染：从 AI SDK `streamText` → `toUIMessageStreamResponse` → part-based 分发渲染 → Artifact 流式内容同步，完整掌握流式数据管道
- 类型安全 Tool Calling：Zod Schema → AI SDK tool() → `InferUITools` → 客户端按 tool 名分发渲染，全链路类型安全
- 自定义设计系统：40+ CSS Custom Properties Token，纯 CSS 实现 dark mode 切换、代码高亮、glass morphism 效果，组件零硬编码颜色
