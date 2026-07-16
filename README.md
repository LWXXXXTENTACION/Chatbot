# DeepSeek Chatbot

一个带 FastAPI + LangGraph 后端的 DeepSeek 聊天应用：流式 UI、工具调用可视化、独立深度搜索、行内来源链接和 Artifact 侧栏。前端基于 Next.js 16、React 19、Zustand 与 Tailwind v4。

## 功能

- **多模型切换** — `deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-chat` (V3) / `deepseek-reasoner` (R1) 一键切换
- **流式输出** — 基于 POST SSE 的增量渲染，Markdown 边流边解析（memo 优化）
- **推理过程可视化** — R1 等推理模型自动展示可折叠的「思考过程」，带 shimmer 动画
- **Supervisor 多 Agent** — Supervisor 分解并分派任务；General Agent 自主使用天气、计算与 Artifact，Research Agent 专职快速联网与 Deep Search，最后由 Supervisor 整合答案
- **行内来源链接** — 重要结论后的 `[[cite:n]]` 自动显示为响应式来源 span link，并在会话重载后保留
- **Artifact 侧栏系统** — 模型用 `createArtifact` 工具产出独立工件；侧栏边流式边渲染，HTML/SVG 可实时预览，代码可一键复制
- **代码高亮** — `rehype-highlight` 语法高亮 + 语言标签 + 复制按钮，配色随明暗主题切换
- **会话状态管理** — Zustand + persist 中间件，多会话历史保存在 `localStorage`
- **键盘友好** — `Enter` 发送，`Shift + Enter` 换行；生成中可随时中断
- **高级感 UI** — 玻璃质感、渐变光晕、消息淡入、自动深色模式

## 核心交互对照（Claude.ai / Cursor）

| 能力 | 实现 |
|---|---|
| 流式渲染 | `fetch` + `ReadableStream` 解析 POST SSE |
| Markdown 增量解析 | `react-markdown` + `remark-gfm`，按内容 memo |
| 代码块高亮 | `rehype-highlight` + 自定义 `CodeBlock`（语言标签/复制） |
| 工具调用可视化 | LangGraph 工具节点；`ToolInvocation` 渲染各状态 |
| Artifact 系统 | `createArtifact` 工具 → `ArtifactCard` + `ArtifactPanel`（预览/源码切换） |
| 对话历史管理 | SQLAlchemy SQLite 业务库 + LangGraph AsyncSqliteSaver |
| 热缓存与限流 | Redis 精确工具缓存 + 原子 Token Bucket（故障自动降级） |

## 快速开始

### 1. 配置 API Key

复制环境变量模板：

```bash
cp .env.local.example .env.local
```

打开 `.env.local`，填入你的 DeepSeek API Key（[在这里获取](https://platform.deepseek.com/api_keys)）：

```
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx
```

### 2. 安装依赖（已安装可跳过）

```bash
npm install
```

### 3. 启动开发服务器

```bash
npm run dev
```

打开 [http://localhost:3000](http://localhost:3000) 即可开始对话。

> **本地代理 / TLS 提示**：如果你的 macOS 上运行了 ClashX / Surge 等代理软件（系统会注入 127.0.0.1:7890），Node 25 的 fetch 在校验 DeepSeek 证书链时可能会报
> `self-signed certificate in certificate chain`。
> 解决方案有两种：
> - **临时**：在 `.env.local` 中追加 `DEEPSEEK_INSECURE_TLS=1`（仅开发环境，跳过对外请求的 TLS 校验）。
> - **干净**：在代理软件里给 `api.deepseek.com` 设置直连规则，并退出代理。

### 4. 生产构建

```bash
npm run build
npm run start
```

## 项目结构

```
src/
├── app/
│   ├── api/chat/route.ts    # 流式 API 路由：DeepSeek + 工具 + 多步
│   ├── layout.tsx           # 全局布局
│   ├── page.tsx             # 主页面（侧边栏 + 聊天区 + Artifact 面板）
│   └── globals.css          # 设计系统 + Markdown 排版 + 代码高亮主题
├── components/
│   ├── Sidebar.tsx          # 会话列表
│   ├── ChatView.tsx         # 消息流容器（useChat + 类型化工具）
│   ├── MessageBubble.tsx    # 单条消息（按 part 顺序渲染）
│   ├── ChatComposer.tsx     # 自适应输入框
│   ├── ModelSelector.tsx    # 模型下拉选择
│   ├── Markdown.tsx         # Markdown 渲染（memo + rehype-highlight）
│   ├── CodeBlock.tsx        # 代码块外壳（语言标签 + 复制）
│   ├── ToolInvocation.tsx   # 工具调用可视化（状态/参数/结果）
│   ├── ArtifactCard.tsx     # 聊天内工件卡片（流式 + 自动开面板）
│   └── ArtifactPanel.tsx    # 右侧 Artifact 面板（预览/源码）
└── lib/
    ├── models.ts            # 模型清单
    ├── tools.ts             # 服务端工具定义（zod schema）
    ├── store.ts             # Zustand 会话/工件状态（persist）
    └── types.ts             # 共享类型（含工具类型推断）
```

## 添加更多模型

编辑 `src/lib/models.ts`，在 `DEEPSEEK_MODELS` 数组中追加：

```ts
{
  id: "deepseek-coder",       // 必须是 DeepSeek 平台支持的 model id
  name: "DeepSeek Coder",
  description: "代码专精模型",
  badge: "代码",
}
```

并把 `id` 加入 `DeepSeekModelId` 联合类型即可。

## 技术栈

- **Next.js 16** (App Router, Turbopack)
- **React 19**
- **Tailwind CSS v4**
- **Vercel AI SDK v6** (`ai`, `@ai-sdk/react`, `@ai-sdk/deepseek`)
- **TypeScript 5**
- **lucide-react** 图标
- **react-markdown** + **remark-gfm**
