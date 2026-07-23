"""从环境变量加载应用配置；模型 ID 与 ``src/lib/models.ts`` 保持一致。"""

import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_INSECURE_TLS = os.getenv("DEEPSEEK_INSECURE_TLS", "0") == "1"
# 两个 SQLite 文件职责不同：DATABASE_URL 是业务事实，CHECKPOINT_DB_PATH 是可回放
# 的 Graph State；stream 级 checkpoint hot cache 只加速读取，不替代这个持久副本。
DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///./chatbot.db"
)
CHECKPOINT_DB_PATH = os.getenv("CHECKPOINT_DB_PATH", "./checkpoints.db")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "1").lower() not in {"0", "false", "no"}
AUTO_MIGRATE = os.getenv("AUTO_MIGRATE", "1").lower() not in {"0", "false", "no"}
# 五层 Context Engineering 阈值。checkpoint 保留 thread 状态，这些配置只控制
# 每次真正发送给模型的工作上下文和何时生成 summary/memory。
CONTEXT_MAX_INPUT_TOKENS = max(
    1024,
    int(os.getenv("CONTEXT_MAX_INPUT_TOKENS", "32000")),
)
CONTEXT_MICROCOMPACT_TTL_SECONDS = max(
    60,
    int(os.getenv("CONTEXT_MICROCOMPACT_TTL_SECONDS", "1800")),
)
CONTEXT_SESSION_MEMORY_RATIO = float(
    os.getenv("CONTEXT_SESSION_MEMORY_RATIO", "0.45")
)
CONTEXT_COLLAPSE_RATIO = float(os.getenv("CONTEXT_COLLAPSE_RATIO", "0.62"))
CONTEXT_FULL_COMPACT_RATIO = float(
    os.getenv("CONTEXT_FULL_COMPACT_RATIO", "0.82")
)
CONTEXT_PTL_TRUNCATION_RATIO = float(
    os.getenv("CONTEXT_PTL_TRUNCATION_RATIO", "0.95")
)
CONTEXT_KEEP_RECENT_TURNS = max(
    1,
    int(os.getenv("CONTEXT_KEEP_RECENT_TURNS", "2")),
)

# 被时间顺序摘要折叠掉的完整旧轮次，会进入一个可重建的语义索引。索引故障时
# LangGraph 主链 fail-open，context_summary / session_memory 仍可独立工作。
CONTEXT_INDEX_ENABLED = os.getenv("CONTEXT_INDEX_ENABLED", "1").lower() not in {
    "0", "false", "no",
}
CONTEXT_INDEX_PATH = os.getenv("CONTEXT_INDEX_PATH", "./context_index")
CONTEXT_INDEX_COLLECTION = os.getenv(
    "CONTEXT_INDEX_COLLECTION", "chat_context_v1"
)
CONTEXT_EMBED_MODEL = os.getenv(
    "CONTEXT_EMBED_MODEL", "BAAI/bge-small-zh-v1.5"
)
# 在线聊天只使用已经下载到本机的嵌入模型。模型缺失时必须立即跳过语义召回，
# 不能让 Hugging Face 下载占住 Graph 热路径；重建脚本会显式允许下载。
CONTEXT_EMBED_ALLOW_DOWNLOAD = os.getenv(
    "CONTEXT_EMBED_ALLOW_DOWNLOAD", "0"
).lower() in {"1", "true", "yes"}
CONTEXT_INDEX_VERSION = os.getenv("CONTEXT_INDEX_VERSION", "v1")
CONTEXT_RETRIEVAL_TOP_K = max(
    1, int(os.getenv("CONTEXT_RETRIEVAL_TOP_K", "8"))
)
CONTEXT_RETRIEVAL_MAX_CHUNKS = max(
    1, int(os.getenv("CONTEXT_RETRIEVAL_MAX_CHUNKS", "4"))
)
CONTEXT_RETRIEVAL_MAX_TOKENS = max(
    128, int(os.getenv("CONTEXT_RETRIEVAL_MAX_TOKENS", "1600"))
)
CONTEXT_RETRIEVAL_SCORE_THRESHOLD = float(
    os.getenv("CONTEXT_RETRIEVAL_SCORE_THRESHOLD", "0.35")
)

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

DeepSeekModelId = Literal[
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
]

DEFAULT_MODEL: DeepSeekModelId = "deepseek-v4-flash"

# Models that do NOT support function calling
NO_TOOLS_MODELS: set[DeepSeekModelId] = {"deepseek-reasoner"}

ALLOWED_MODELS: set[DeepSeekModelId] = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
}

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# Default system prompt (Chinese, mirrors the TypeScript version)
DEFAULT_SYSTEM_PROMPT = """\
你是一个乐于助人的中文 AI 助手，界面风格类似 Claude.ai。
回答简洁、准确，使用 Markdown 格式化，代码要标注语言。"""


def validate_model(model_id: str) -> DeepSeekModelId:
    """Validate and return a model ID, falling back to DEFAULT_MODEL."""
    if model_id in ALLOWED_MODELS:
        return model_id  # type: ignore[return-value]
    return DEFAULT_MODEL


def tools_enabled(model_id: DeepSeekModelId) -> bool:
    """Check whether function calling is supported for the given model."""
    return model_id not in NO_TOOLS_MODELS
