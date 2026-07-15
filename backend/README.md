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

```
WebSocket /ws
  ↓
main.py (FastAPI)
  ↓
graph.ainvoke(state, config)
  ├── main_agent (one LLM handles every user-facing task)
  │     └── emit: text_*, reasoning_*, tool_call_* events
  ├── routing (should_continue)
  └── tools (weather / calculate / artifact / deep_search)
        ├── direct tools run without another agent
        └── deep_search_agent → parallel web_search → cited brief
```

Authenticated conversations compile the graph once with `AsyncSqliteSaver`.
The conversation ID is the LangGraph `thread_id`; stream callbacks and cache
clients are runtime context and are never serialized into checkpoints.

`deep_search` is capped at one delegation per user turn. Its numbered sources
are attached to the final assistant message and streamed in a `sources` event.

## WebSocket Protocol

Connect to `ws://localhost:8000/ws`.

**Send a message:**
```json
{
  "type": "send",
  "messages": [{"role": "user", "content": "Hello"}],
  "model": "deepseek-v4-flash"
}
```

**Stop generation:**
```json
{"type": "stop"}
```

**Server events:** `text_start`, `text_delta`, `text_end`, `reasoning_start`, `reasoning_delta`, `reasoning_end`, `tool_call_start`, `tool_call_delta`, `tool_call_end`, `tool_result`, `sources`, `done`, `error`, `pong`

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```
