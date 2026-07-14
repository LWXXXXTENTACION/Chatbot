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

Copy `.env` and edit:

```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_INSECURE_TLS=1  # Enable if behind local TLS proxy (ClashX/Surge)
```

## Architecture

```
WebSocket /ws
  ↓
main.py (FastAPI)
  ↓
graph.ainvoke(state, config)
  ├── chat_node (LLM call + streaming)
  │     └── emit: text_*, reasoning_*, tool_call_* events
  ├── routing (should_continue)
  └── tool_node (execute tools)
        └── emit: tool_result events
```

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

**Server events:** `text_start`, `text_delta`, `text_end`, `reasoning_start`, `reasoning_delta`, `reasoning_end`, `tool_call_start`, `tool_call_delta`, `tool_call_end`, `tool_result`, `done`, `error`, `pong`

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest
```
