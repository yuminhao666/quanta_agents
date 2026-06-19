from __future__ import annotations

import json
import re
from typing import Any


def strip_fences(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    value = strip_fences(text)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = value.find("{")
        while start >= 0:
            try:
                parsed, _ = decoder.raw_decode(value[start:])
                break
            except json.JSONDecodeError:
                start = value.find("{", start + 1)
        else:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("LLM output is not a JSON object")
    return parsed
