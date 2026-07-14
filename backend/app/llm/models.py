"""
Model definitions — mirrors src/lib/models.ts.
"""

from dataclasses import dataclass

from app.config import DeepSeekModelId


@dataclass
class DeepSeekModel:
    id: DeepSeekModelId
    name: str
    description: str
    badge: str | None = None
    deprecated: str | None = None


DEEPSEEK_MODELS: list[DeepSeekModel] = [
    DeepSeekModel(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        description="最新一代轻量模型，响应快、成本低，适合日常对话",
        badge="极速",
    ),
    DeepSeekModel(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        description="最新一代旗舰模型，能力强，适合复杂任务与推理",
        badge="旗舰",
    ),
    DeepSeekModel(
        id="deepseek-chat",
        name="DeepSeek V3",
        description="上一代通用对话模型（将于 2026-07-24 停用）",
        badge="通用",
        deprecated="2026-07-24",
    ),
    DeepSeekModel(
        id="deepseek-reasoner",
        name="DeepSeek R1",
        description="上一代推理模型，会先思考再回答（将于 2026-07-24 停用）",
        badge="推理",
        deprecated="2026-07-24",
    ),
]


def get_model(id: str) -> DeepSeekModel:
    """Look up a model by ID, falling back to the first entry."""
    for m in DEEPSEEK_MODELS:
        if m.id == id:
            return m
    return DEEPSEEK_MODELS[0]
