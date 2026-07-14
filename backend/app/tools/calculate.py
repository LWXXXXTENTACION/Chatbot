"""
Arithmetic calculator tool — mirrors the calculate tool in src/lib/tools.ts.
Uses regex-sanitized eval with the same safety constraints.
"""

import re

from langchain_core.tools import tool


@tool
def calculate(expression: str) -> dict:
    """计算一个数学表达式。支持 + - * / ( ) 和小数。当用户需要算术结果时调用。

    Args:
        expression: 要计算的算式，例如 (12 + 8) * 3 / 2
    """
    cleaned = re.sub(r"[^0-9+\-*/().\s]", "", expression)
    if not cleaned.strip():
        return {"expression": expression, "error": "表达式为空或包含非法字符"}

    try:
        # Constrained to arithmetic chars above, so eval is safe here.
        result = eval(cleaned, {"__builtins__": {}}, {})
        if not isinstance(result, (int, float)) or not _is_finite(result):
            return {"expression": cleaned, "error": "无法计算出有效数字"}
        return {"expression": cleaned, "result": result}
    except Exception:
        return {"expression": expression, "error": "表达式无法解析"}


def _is_finite(value: float) -> bool:
    """Check if a numeric value is finite."""
    import math
    return not (math.isnan(value) or math.isinf(value))
