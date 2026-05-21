#!/usr/bin/env python3
"""Minimal SiliconFlow Qwen timeout reproduction.

Run:

    export SILICONFLOW_API_KEY="sk-..."
    python3 examples/siliconflow_qwen35_4b_timeout_repro.py

What this script does:

- Uses only Python standard library.
- Sends the same minimal chat-completions request to the same endpoint.
- Does not print the API key.
- Compares:
  - Qwen/Qwen3.5-4B with SiliconFlow's default thinking behavior
  - Qwen/Qwen3.5-4B with enable_thinking=false
  - Qwen/Qwen2.5-7B-Instruct
  - Qwen/Qwen3.5-9B with enable_thinking=false

Observed results from our runs on 2026-05-21:

    Endpoint: https://api.siliconflow.cn/v1/chat/completions
    GET /models: HTTP 200 in 0.28s; all three model IDs were listed.

    Qwen/Qwen3.5-4B, minimal request without enable_thinking:
      Run A: TimeoutError: The read operation timed out after 15.14s.
      Run B: HTTP 200 in 0.50s, but assistant content was empty and the request
             consumed 8 completion tokens.

    Qwen/Qwen3.5-4B, same request plus enable_thinking=false:
      HTTP 200 in 0.35-0.38s, assistant content: "OK"

    Qwen/Qwen2.5-7B-Instruct, same minimal request:
      HTTP 200 in 0.40-0.47s, assistant content: "OK."

    Qwen/Qwen3.5-9B, same request plus enable_thinking=false:
      HTTP 200 in 0.27s, assistant content: "OK"

Interpretation:

    This does not look like a local network problem or an API-key/quota problem:
    the same API key, same base URL, same endpoint, and same minimal request work
    for Qwen/Qwen2.5-7B-Instruct. Qwen/Qwen3.5-4B also works when
    enable_thinking=false is explicitly supplied. The failure is specifically the
    default Qwen/Qwen3.5-4B request: it either times out without HTTP
    429/401/400, or returns HTTP 200 with empty assistant content.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
API_KEY_ENV = os.environ.get("SILICONFLOW_API_KEY_ENV", "SILICONFLOW_API_KEY")
TIMEOUT_SECONDS = float(os.environ.get("SILICONFLOW_TIMEOUT_SECONDS", "15"))


def request_json(method: str, path: str, api_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method=method,
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": response.status,
                "seconds": time.time() - started,
                "bytes": len(body),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_error": exc.code,
            "seconds": time.time() - started,
            "bytes": len(body),
            "body": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "seconds": time.time() - started,
        }


def print_models_probe(api_key: str) -> None:
    result = request_json("GET", "/models", api_key)
    summary = {key: value for key, value in result.items() if key != "body"}
    print("models_endpoint", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if not result.get("ok"):
        print("models_endpoint_body_head", str(result.get("body", ""))[:300].replace("\n", " "))
        return

    body = json.loads(result["body"])
    listed_ids = {
        item.get("id")
        for item in body.get("data", [])
        if isinstance(item, dict)
    }
    for model in ["Qwen/Qwen3.5-4B", "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3.5-9B"]:
        print(f"model_listed {model}: {model in listed_ids}")


def chat_probe(name: str, api_key: str, payload: dict[str, Any]) -> None:
    result = request_json("POST", "/chat/completions", api_key, payload)
    summary = {key: value for key, value in result.items() if key != "body"}

    if result.get("ok"):
        body = json.loads(result["body"])
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = "<missing choices[0].message.content>"
        usage = body.get("usage")
        print(
            "chat_probe",
            name,
            json.dumps(
                {
                    **summary,
                    "response_model": body.get("model"),
                    "assistant_content": content,
                    "usage": usage,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    else:
        body_head = str(result.get("body", ""))[:300].replace("\n", " ")
        print("chat_probe", name, json.dumps({**summary, "body_head": body_head}, ensure_ascii=False, sort_keys=True))


def main() -> int:
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        print(f"Missing API key. Set {API_KEY_ENV}=sk-...", file=sys.stderr)
        return 2

    print(f"base_url={BASE_URL}")
    print(f"api_key_env={API_KEY_ENV}")
    print(f"timeout_seconds={TIMEOUT_SECONDS:g}")
    print_models_probe(api_key)

    minimal_messages = [{"role": "user", "content": "Say OK."}]
    probes = [
        (
            "qwen35_4b_minimal",
            {
                "model": "Qwen/Qwen3.5-4B",
                "messages": minimal_messages,
                "max_tokens": 8,
            },
        ),
        (
            "qwen35_4b_enable_thinking_false",
            {
                "model": "Qwen/Qwen3.5-4B",
                "messages": minimal_messages,
                "max_tokens": 8,
                "enable_thinking": False,
            },
        ),
        (
            "qwen25_7b_minimal",
            {
                "model": "Qwen/Qwen2.5-7B-Instruct",
                "messages": minimal_messages,
                "max_tokens": 8,
            },
        ),
        (
            "qwen35_9b_enable_thinking_false",
            {
                "model": "Qwen/Qwen3.5-9B",
                "messages": minimal_messages,
                "max_tokens": 8,
                "enable_thinking": False,
            },
        ),
    ]
    for name, payload in probes:
        chat_probe(name, api_key, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
