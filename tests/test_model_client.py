import json
import urllib.error

import pytest

from ai_slurm.ai.client import ModelClient


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def test_openai_compatible_client_posts_chat_completion_request():
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

    client = ModelClient(
        api_key="secret",
        provider="openai-compatible",
        base_url="https://api.example.test/v1",
        model="model-a",
        urlopen=fake_urlopen,
    )
    content = client.chat_json(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Summarize."},
        ]
    )

    assert content == '{"one_line_summary":"ok"}'
    assert captured["url"] == "https://api.example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "model-a"
    assert captured["body"]["max_tokens"] == 512
    assert "enable_thinking" not in captured["body"]
    assert captured["body"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_client_allows_model_and_extra_body_override():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = ModelClient(
        api_key="secret",
        provider="openai-compatible",
        base_url="https://api.example.test/v1",
        model="model-b",
        max_tokens=64,
        extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        urlopen=fake_urlopen,
    )
    client.chat_json([{"role": "user", "content": "x"}])

    assert captured["body"]["model"] == "model-b"
    assert captured["body"]["max_tokens"] == 64
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["reasoning_effort"] == "high"


def test_openai_compatible_client_can_send_enable_thinking_when_requested():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = ModelClient(
        api_key="secret",
        provider="openai-compatible",
        base_url="https://api.example.test/v1",
        model="model-a",
        enable_thinking=False,
        urlopen=fake_urlopen,
    )
    client.chat_json([{"role": "user", "content": "x"}])

    assert captured["body"]["enable_thinking"] is False


def test_openai_compatible_client_can_disable_response_format():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = ModelClient(
        api_key="secret",
        provider="openai-compatible",
        base_url="https://api.example.test/v1",
        model="model-a",
        response_format=None,
        urlopen=fake_urlopen,
    )
    client.chat_json([{"role": "user", "content": "x"}])

    assert "response_format" not in captured["body"]


def test_anthropic_compatible_client_posts_messages_request():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"content": [{"type": "text", "text": '{"ok":true}'}]})

    client = ModelClient(
        api_key="secret",
        provider="anthropic-compatible",
        base_url="https://api.example.test/anthropic",
        model="model-c",
        max_tokens=128,
        urlopen=fake_urlopen,
    )

    content = client.chat_json(
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Summarize."},
        ]
    )

    assert content == '{"ok":true}'
    assert captured["url"] == "https://api.example.test/anthropic/v1/messages"
    assert captured["headers"]["X-api-key"] == "secret"
    assert captured["headers"]["Anthropic-version"] == "2023-06-01"
    assert captured["body"]["model"] == "model-c"
    assert captured["body"]["max_tokens"] == 128
    assert captured["body"]["system"] == "Return JSON."
    assert captured["body"]["messages"] == [{"role": "user", "content": "Summarize."}]


def test_model_client_reads_protocol_base_url_and_key_env_from_config(isolated_home, monkeypatch):
    from ai_slurm.config import config_path

    monkeypatch.setenv("KIMI_API_KEY", "kimi-secret")
    config_path().write_text(
        "[ai]\n"
        "provider = \"anthropic-compatible\"\n"
        "api_key_env = \"KIMI_API_KEY\"\n"
        "base_url = \"https://api.kimi.com/coding/\"\n"
        "model = \"kimi-for-coding\"\n"
        "max_tokens = \"256\"\n"
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"content": [{"type": "text", "text": '{"ok":true}'}]})

    client = ModelClient(urlopen=fake_urlopen)
    client.chat_json([{"role": "user", "content": "x"}])

    assert captured["url"] == "https://api.kimi.com/coding/v1/messages"
    assert captured["headers"]["X-api-key"] == "kimi-secret"
    assert captured["body"]["model"] == "kimi-for-coding"
    assert captured["body"]["max_tokens"] == 256


def test_model_client_reads_escaped_extra_body_json_from_config(isolated_home, monkeypatch):
    from ai_slurm.config import config_path

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    config_path().write_text(
        "[ai]\n"
        "provider = \"openai-compatible\"\n"
        "api_key_env = \"DEEPSEEK_API_KEY\"\n"
        "base_url = \"https://api.deepseek.com\"\n"
        "model = \"deepseek-v4-pro\"\n"
        "extra_body_json = \"{\\\"thinking\\\":{\\\"type\\\":\\\"enabled\\\"}}\"\n"
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse({"choices": [{"message": {"content": '{"ok":true}'}}]})

    client = ModelClient(urlopen=fake_urlopen)
    client.chat_json([{"role": "user", "content": "x"}])

    assert captured["body"]["thinking"] == {"type": "enabled"}


def test_model_client_requires_configured_base_url_and_model(isolated_home, monkeypatch):
    monkeypatch.delenv("AI_SLURM_AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_SLURM_AI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="Missing AI base URL"):
        ModelClient(api_key="secret")

    with pytest.raises(RuntimeError, match="Missing AI model"):
        ModelClient(api_key="secret", base_url="https://api.example.test/v1")


def test_model_client_raises_useful_error_on_http_failure():
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=FakeHttpErrorBody(b'{"message":"bad key"}'),
        )

    client = ModelClient(api_key="secret", base_url="https://api.example.test/v1", model="model-a", urlopen=fake_urlopen)

    with pytest.raises(RuntimeError, match="AI model request failed"):
        client.chat_json([{"role": "user", "content": "x"}])


def test_model_client_raises_useful_error_on_timeout():
    def fake_urlopen(request, timeout):
        raise TimeoutError("read timed out")

    client = ModelClient(api_key="secret", base_url="https://api.example.test/v1", model="model-a", urlopen=fake_urlopen)

    with pytest.raises(RuntimeError, match="timed out"):
        client.chat_json([{"role": "user", "content": "x"}])


class FakeHttpErrorBody:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass
