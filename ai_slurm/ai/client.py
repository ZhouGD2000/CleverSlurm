import json
import urllib.error
import urllib.request
from collections.abc import Callable

from ai_slurm.config import ai_api_key, ai_model


SILICONFLOW_CHAT_COMPLETIONS_URL = "https://api.siliconflow.cn/v1/chat/completions"


class SiliconFlowClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = SILICONFLOW_CHAT_COMPLETIONS_URL,
        timeout: int = 60,
        urlopen: Callable | None = None,
    ):
        self.api_key = api_key or ai_api_key()
        if not self.api_key:
            raise RuntimeError(
                "Missing SiliconFlow API key. Set SILICONFLOW_API_KEY or [ai].api_key in ~/.ai-slurm/config.toml."
            )
        self.model = model or ai_model()
        self.base_url = base_url
        self.timeout = timeout
        self.urlopen = urlopen or urllib.request.urlopen

    def chat_json(self, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "top_p": 0.7,
            "max_tokens": 2048,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.fp.read().decode(errors="replace") if exc.fp else str(exc)
            raise RuntimeError(f"SiliconFlow request failed: HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"SiliconFlow request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"SiliconFlow request timed out: {exc}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("SiliconFlow response did not contain choices[0].message.content") from exc
