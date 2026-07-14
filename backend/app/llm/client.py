"""
DeepSeek LLM client factory.
Uses langchain-openai's ChatOpenAI pointed at DeepSeek's API.
"""

import httpx
from langchain_openai import ChatOpenAI

from app.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_INSECURE_TLS,
    DeepSeekModelId,
    tools_enabled,
)


def create_deepseek_chat(
    model_id: DeepSeekModelId,
    temperature: float = 0.6,
) -> ChatOpenAI:
    """
    Create a ChatOpenAI instance pointed at the DeepSeek API.

    When DEEPSEEK_INSECURE_TLS=1, configures httpx with verify=False
    to bypass local proxy (ClashX/Surge) TLS interception.
    """
    http_client = None
    if DEEPSEEK_INSECURE_TLS:
        http_client = httpx.Client(verify=False)

    kwargs: dict = dict(
        model=model_id,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
    )

    # deepseek-reasoner uses a separate reasoning_effort param
    if model_id == "deepseek-reasoner":
        kwargs["model_kwargs"] = {"reasoning_effort": "medium"}

    if http_client is not None:
        kwargs["http_client"] = http_client

    return ChatOpenAI(**kwargs)
