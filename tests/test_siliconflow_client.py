import json
import urllib.error

import pytest

from ai_slurm.ai.client import SiliconFlowClient


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def test_siliconflow_client_posts_chat_completion_request():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse(
            {
                "choices": [
                    {"message": {"content": '{"one_line_summary":"ok"}'}}
                ]
            }
        )

    client = SiliconFlowClient(api_key="secret", urlopen=fake_urlopen)
    content = client.chat_json(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Summarize."},
        ]
    )

    assert content == '{"one_line_summary":"ok"}'
    assert captured["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "Qwen/Qwen3.5-4B"
    assert captured["body"]["enable_thinking"] is False
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_siliconflow_client_raises_useful_error_on_http_failure():
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=FakeHttpErrorBody(b'{"message":"bad key"}'),
        )

    client = SiliconFlowClient(api_key="secret", urlopen=fake_urlopen)

    with pytest.raises(RuntimeError, match="SiliconFlow request failed"):
        client.chat_json([{"role": "user", "content": "x"}])


def test_siliconflow_client_raises_useful_error_on_timeout():
    def fake_urlopen(request, timeout):
        raise TimeoutError("read timed out")

    client = SiliconFlowClient(api_key="secret", urlopen=fake_urlopen)

    with pytest.raises(RuntimeError, match="timed out"):
        client.chat_json([{"role": "user", "content": "x"}])


class FakeHttpErrorBody:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass
