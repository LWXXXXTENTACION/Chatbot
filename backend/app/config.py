"""
Application configuration loaded from environment variables.
Mirrors the model definitions in src/lib/models.ts.
"""

import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_INSECURE_TLS = os.getenv("DEEPSEEK_INSECURE_TLS", "0") == "1"
# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite+aiosqlite:///./chatbot.db"
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
