"""
Weather lookup tool — mirrors the getWeather tool in src/lib/tools.ts.
"""

import random
from datetime import datetime, timezone

from langchain_core.tools import tool

WEATHER_SAMPLE: dict[str, dict[str, int | str]] = {
    "北京": {"tempC": 24, "condition": "晴"},
    "上海": {"tempC": 27, "condition": "多云"},
    "深圳": {"tempC": 31, "condition": "雷阵雨"},
    "东京": {"tempC": 22, "condition": "晴"},
    "伦敦": {"tempC": 15, "condition": "小雨"},
}

CONDITIONS = ["晴", "多云", "阴", "小雨"]


@tool
def get_weather(city: str) -> dict:
    """查询某个城市当前的天气。当用户询问天气、温度、是否下雨时调用。

    Args:
        city: 城市名，例如 北京、上海、东京
    """
    hit = WEATHER_SAMPLE.get(city.strip())
    if hit:
        tempC = hit["tempC"]
        condition = hit["condition"]
    else:
        tempC = random.randint(10, 30)
        condition = random.choice(CONDITIONS)

    return {
        "city": city.strip(),
        "tempC": tempC,
        "condition": condition,
        "humidity": random.randint(40, 90),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
