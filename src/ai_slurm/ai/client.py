import json
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urljoin

from ai_slurm.ai.json_utils import parse_json_object
from ai_slurm.config import (
    ai_anthropic_version,
    ai_api_key,
    ai_base_url,
    ai_enable_thinking,
    ai_extra_body,
    ai_fallback_models,
    ai_max_tokens,
    ai_model,
    ai_provider,
    ai_request_retries,
    ai_response_format,
    ai_temperature,
    ai_timeout_seconds,
    ai_top_p,
)


OPENAI_COMPATIBLE = "openai-compatible"
ANTHROPIC_COMPATIBLE = "anthropic-compatible"
_CONFIG_DEFAULT = object()


class ModelRequestError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, retry_same_model: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.retry_same_model = retry_same_model


def _endpoint(base_url: str, suffix: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith(suffix.strip("/")):
        return stripped
    return urljoin(stripped + "/", suffix.lstrip("/"))


def _message_content(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


class ModelClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        extra_body: dict | None = None,
        response_format: str | None | object = _CONFIG_DEFAULT,
        fallback_models: list[str] | None = None,
        anthropic_version: str | None = None,
        timeout: int | None = None,
        request_retries: int | None = None,
        urlopen: Callable | None = None,
    ):
        self.provider = (provider or ai_provider()).strip().lower()
        if self.provider not in {OPENAI_COMPATIBLE, ANTHROPIC_COMPATIBLE}:
            raise RuntimeError(
                "Unsupported AI provider protocol. Use 'openai-compatible' or 'anthropic-compatible'."
            )
        self.api_key = api_key or ai_api_key()
        if not self.api_key:
            raise RuntimeError(
                "Missing AI API key. Set AI_SLURM_AI_API_KEY, configure [ai].api_key_env, or set [ai].api_key."
            )
        self.base_url = base_url or ai_base_url()
        if not self.base_url:
            raise RuntimeError("Missing AI base URL. Set AI_SLURM_AI_BASE_URL or [ai].base_url.")
        self.model = model or ai_model()
        if not self.model:
            raise RuntimeError("Missing AI model. Set AI_SLURM_AI_MODEL or [ai].model.")
        self.max_tokens = max_tokens or ai_max_tokens()
        self.enable_thinking = ai_enable_thinking() if enable_thinking is None else enable_thinking
        self.temperature = ai_temperature() if temperature is None else temperature
        self.top_p = ai_top_p() if top_p is None else top_p
        self.extra_body = ai_extra_body() if extra_body is None else extra_body
        self.response_format = ai_response_format() if response_format is _CONFIG_DEFAULT else response_format
        self.fallback_models = ai_fallback_models() if fallback_models is None else fallback_models
        self.anthropic_version = anthropic_version or ai_anthropic_version()
        self.timeout = ai_timeout_seconds() if timeout is None else timeout
        self.request_retries = ai_request_retries() if request_retries is None else max(0, request_retries)
        self.urlopen = urlopen or urllib.request.urlopen
        self.last_model: str | None = None

    def chat_json(self, messages: list[dict]) -> str:
        errors = []
        for model in self._candidate_models():
            for attempt in range(self.request_retries + 1):
                try:
                    content = self._chat_json_once(messages, model=model)
                except ModelRequestError as exc:
                    if not exc.retryable:
                        raise
                    if exc.retry_same_model and attempt < self.request_retries:
                        continue
                    errors.append(f"{model}: {exc}")
                    break
                try:
                    parse_json_object(content, error_label="AI model response")
                except ValueError as exc:
                    errors.append(f"{model}: {exc}")
                    break
                self.last_model = model
                return content
        detail = " | ".join(errors) if errors else "no models configured"
        raise RuntimeError(f"AI model request failed for all configured models: {detail}")

    def _candidate_models(self) -> list[str]:
        seen = set()
        models = []
        for model in [self.model, *self.fallback_models]:
            if model and model not in seen:
                models.append(model)
                seen.add(model)
        return models

    def _chat_json_once(self, messages: list[dict], *, model: str) -> str:
        if self.provider == ANTHROPIC_COMPATIBLE:
            request = self._anthropic_request(messages, model=model)
        else:
            request = self._openai_request(messages, model=model)
        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.fp.read().decode(errors="replace") if exc.fp else str(exc)
            retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
            raise ModelRequestError(f"AI model request failed: HTTP {exc.code}: {detail}", retryable=retryable) from exc
        except urllib.error.URLError as exc:
            raise ModelRequestError(
                f"AI model request failed: {exc.reason}",
                retryable=True,
                retry_same_model=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelRequestError(f"AI model request timed out: {exc}", retryable=True) from exc

        if self.provider == ANTHROPIC_COMPATIBLE:
            return self._parse_anthropic_response(body)
        return self._parse_openai_response(body)

    def _openai_request(self, messages: list[dict], *, model: str) -> urllib.request.Request:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if self.response_format:
            payload["response_format"] = {"type": self.response_format}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        if self.enable_thinking is not None:
            payload["enable_thinking"] = self.enable_thinking
        payload.update(self.extra_body)
        return urllib.request.Request(
            _endpoint(self.base_url, "/chat/completions"),
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

    def _anthropic_request(self, messages: list[dict], *, model: str) -> urllib.request.Request:
        system_parts = [_message_content(message) for message in messages if message.get("role") == "system"]
        anthropic_messages = [
            {
                "role": "assistant" if message.get("role") == "assistant" else "user",
                "content": _message_content(message),
            }
            for message in messages
            if message.get("role") != "system"
        ]
        payload = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.top_p is not None:
            payload["top_p"] = self.top_p
        payload.update(self.extra_body)
        return urllib.request.Request(
            _endpoint(self.base_url, "/v1/messages"),
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.anthropic_version,
            },
            method="POST",
        )

    @staticmethod
    def _parse_openai_response(body: dict) -> str:
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("AI model response did not contain choices[0].message.content") from exc

    @staticmethod
    def _parse_anthropic_response(body: dict) -> str:
        try:
            content = body["content"]
        except KeyError as exc:
            raise RuntimeError("AI model response did not contain content") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {None, "text"}
            ]
            text = "\n".join(part for part in texts if part)
            if text:
                return text
        raise RuntimeError("AI model response content did not contain text")


AIClient = ModelClient
