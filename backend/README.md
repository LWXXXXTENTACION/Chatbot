# DeepSeek Chatbot — LangGraph Python Backend

Python backend for the DeepSeek Chatbot, using LangGraph for agent orchestration.

## Quick Start

```bash
cd backend
pip install -e .
uvicorn app.main:app --reload --port 8000
```

Or from the project root:

```bash
npm run backend
# or to start both frontend + backend:
npm run dev:all
```

## Configuration

Configure the backend environment:

```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_INSECURE_TLS=1  # Enable if behind local TLS proxy (ClashX/Surge)
DATABASE_URL=sqlite+aiosqlite:///./chatbot.db
CHECKPOINT_DB_PATH=./checkpoints.db
REDIS_URL=redis://localhost:6379/0
REDIS_ENABLED=1
AUTO_MIGRATE=1
CONTEXT_MAX_INPUT_TOKENS=32000
CONTEXT_MICROCOMPACT_TTL_SECONDS=1800
CONTEXT_SESSION_MEMORY_RATIO=0.45
CONTEXT_COLLAPSE_RATIO=0.62
CONTEXT_FULL_COMPACT_RATIO=0.82
CONTEXT_PTL_TRUNCATION_RATIO=0.95
CONTEXT_KEEP_RECENT_TURNS=2
```

`chatbot.db` is the business source of truth for users, conversations, and UI
messages. `checkpoints.db` stores LangGraph thread state and can be rebuilt from
the business database. Both files are local runtime state and are ignored by
Git.

Start the optional Redis cache locally from the repository root:

```bash
docker compose up -d redis
```

If Redis is unavailable, tool caching becomes a miss and rate limiting falls
back to the current process; chat remains available. Exact cache TTLs are 60s
for weather, 10m for deep search, and 24h for calculations. Artifact creation
is never cached.

SQLite migrations run automatically by default. They can also be applied
explicitly before startup:

```bash
cd backend
python -m app.database.migrate
```

Set `AUTO_MIGRATE=0` only when migrations are managed separately.

## Architecture

The backend has one authenticated chat entry point: `POST /api/chat/stream`.
`main.py` only owns application resources and router registration; request
validation, persistence, workflow execution, model streaming, and tool
execution are separate modules.

The main graph implements a Supervisor pattern with two specialized workers:

```text
START
  │
  ▼
prepare_turn  reset turn-local coordination state
  │
  ▼
context_manager  evaluate pressure and compact durable working context
  │
  ▼
supervisor    analyze, decompose, and assign one worker
  │
  ├── general_agent ─── weather / calculate / artifact tools
  │
  └── research_agent ── web_search / deep_search only
              │
              ▼
supervisor_finalize  integrate the worker result
              │
              ▼
             END
```

The Supervisor returns an auditable assignment with `route`, `task`, and
`reason`. Explicit `web` and `deep` modes deterministically select the Research
Agent; automatic mode uses the Supervisor model with a deterministic fallback
when its JSON cannot be parsed.

The General Agent is an isolated, bounded tool-using subgraph:

```text
START → prepare → agent ── no tool calls ───────────────→ END
                    │
                    └── tool calls → tools → agent
                                      (maximum 3 rounds)
```

It can use weather, calculation, and artifact tools, but it cannot call any
search tool. Its intermediate prose stays internal; its tool-call and tool-
result messages are returned to the main graph so the live UI, checkpoint, and
business database contain the same trace.

The Research Agent exclusively owns `web_search` and `deep_search`. Fast mode
runs one web query. Deep/automatic research invokes the inspectable DAG below:

Deep search is also an inspectable LangGraph DAG rather than an opaque helper:

```text
START → plan_queries → search_sources → synthesize_brief → END
              (1-3)       (parallel, max 8 sources)
```

It is compiled with `checkpointer=False` because it is a stateless per-turn
sub-workflow with no interrupts or cross-turn memory.

Both worker subgraphs are compiled with `checkpointer=False`. They are bounded
per-turn workspaces with no interrupts or cross-turn memory; the parent
Supervisor graph owns conversation persistence.

### State and runtime boundaries

`AgentInput` contains API-owned values: messages, model, system prompt, user,
and conversation IDs. `AgentState` adds explicit coordination fields:

```text
supervisor_decision  {route, task, reason}
active_agent         supervisor | general_agent | research_agent
completed_agents     ordered list of completed agents
worker_result        internal result passed back to the Supervisor
source_citations     normalized search sources
context_summary      rolling summary of collapsed early turns
session_memory       thread-scoped memory document with durable facts
session_memory_cursor last message extracted into session memory
context_report       applied strategies, token estimates, and overflow flag
error                workflow failure, if any
```

`AgentOutput` exposes only messages and the error consumed by the API. The
`messages` field uses LangGraph's `add_messages` reducer; coordination fields
use replacement semantics and are reset by `prepare_turn` after checkpoint
restoration.

Authenticated conversations compile the main graph once with
`AsyncSqliteSaver`. The conversation ID is the LangGraph `thread_id`.
`prepare_turn` clears turn-local fields after checkpoint restoration. Search
mode and cache clients are request-scoped `AgentRuntimeContext` dependencies
and are never serialized into checkpoints. Streaming is graph-native: nodes
receive LangGraph's injected `StreamWriter` and publish typed `custom` events.

Before the Supervisor runs, `context_manager` applies five ordered pressure
strategies. Ratios are measured against `CONTEXT_MAX_INPUT_TOKENS`:

| Strategy | Default trigger | State change |
|---|---:|---|
| `microcompact` | tool result older than 30m | replace the result payload with a small marker while preserving the `ToolMessage` ID and call metadata |
| `session_memory` | 45% | extract durable preferences, project facts, constraints, conventions, and open work into the thread-scoped memory document |
| `context_collapse` | 62% | summarize the oldest eligible segment and remove that complete segment from checkpoint messages |
| `full_compact` | 82% | roll all eligible old history into the summary while retaining the configured recent turns verbatim |
| `ptl_truncation` | 95% after compression | deterministically remove the earliest complete turn until below the guard or only the latest turn remains |

`context collapse`, `session memory`, and `full compact` share a dedicated
summarizer call. If that call fails or returns malformed JSON, a bounded local
fallback preserves role-labelled excerpts instead of failing the chat turn.
The summary and memory document are injected into later model calls as
historical system context, including the isolated General Agent.

The final `build_context_window` step remains as a non-mutating model-input
safety net. Both persistent removal and transient truncation operate on whole
user turns, so an AI tool call is never separated from its `ToolMessage`.
The complete visible conversation remains in `chatbot.db`; only the Agent's
working history in `checkpoints.db` is compacted. If a checkpoint must be
rebuilt, the business history is loaded and compacted again.

The Research Agent executes one search assignment per turn. Numbered sources
are attached by `supervisor_finalize` to the final assistant message and
streamed in a `sources` event.

### End-to-end data flow

```text
Browser useChatStream
  → Next.js /api/chat proxy
  → FastAPI /api/chat/stream (JWT + rate limit + ownership check)
  → load business history from chatbot.db
  → compare history with the conversation checkpoint
      ├─ synchronized: invoke graph with only the new HumanMessage
      └─ divergent: delete checkpoint and rebuild from business history
  → graph.astream(AgentInput, thread_id=conversation_id,
                  stream_mode=["values", "custom"], version="v2")
  → prepare_turn clears previous coordination state
  → context_manager estimates tokens and applies zero or more compression strategies
      ├─ updates checkpoint messages with same-ID replacements / RemoveMessage
      ├─ persists rolling context_summary + session_memory in AgentState
      └─ emits context_status with before/after metrics
  → Supervisor emits {route, task, reason}
  → selected Worker runs in its isolated subgraph
      ├─ General Agent: bounded autonomous tool loop
      └─ Research Agent: fast search or deep-search DAG
  → Worker returns result + persistable tool trace to shared state
  → Supervisor integrates one final user-facing answer
  → graph nodes publish typed events through LangGraph's custom stream
  → FastAPI consumes custom events and encodes them as POST SSE
  → Next.js pipes bytes without transforming them
  → useChatStream reduces events into UI message parts
  → completed AI/Tool messages are committed to chatbot.db
```

The business database is the source of truth for the UI. `checkpoints.db` is
the durable LangGraph execution state and can be rebuilt when its last message
ID does not match the business history. Redis is only a tool-result cache and
rate-limit backend; it is not conversation memory.

### SSE output contract

Graph nodes emit the typed union declared in `app/graph/events.py`:
`text_start/delta/end`, `reasoning_start/delta/end`,
`tool_call_start/delta/end`, `tool_result`, `sources`, `activity`, and
`context_status`. The API runner adds the terminal `done` or `error` event.
`context_status` contains the exact strategies, before/after token estimates,
pressure ratios, removed-message count, compacted-tool count, and overflow
flag. Field names intentionally match `src/lib/types.ts`; the client maps an
applied strategy chain into the visible Agent workflow timeline.

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```
