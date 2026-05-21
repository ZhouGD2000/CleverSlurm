import json
from collections.abc import Iterator
from typing import Any


def parse_json_object(text: str, *, error_label: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = _extract_first_json_object(text)
        if value is None:
            raise ValueError(f"{error_label} must be valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{error_label} must be a JSON object")
    return value


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    for candidate in _balanced_object_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _balanced_object_candidates(text: str) -> Iterator[str]:
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
                in_string = False
                escaped = False
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                yield text[start : index + 1]
                start = None
