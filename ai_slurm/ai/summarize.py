import json


def parse_summary_json(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("summary must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("summary must be a JSON object")
    return value
