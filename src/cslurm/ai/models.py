"""Bounded model catalog requests; never send chat prompts or follow redirects."""

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit, urlunsplit


class ModelDiscoveryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Invalid model catalog URL")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("HTTPS is required for remote model catalogs")
    return parsed


def endpoints(base_url, format, models_url=""):
    base = _url(base_url)
    if models_url:
        override = _url(models_url)
        if (override.scheme, override.hostname, override.port) != (base.scheme, base.hostname, base.port):
            raise ValueError("Model catalog must use the same origin as the base URL")
        return [(models_url, format)]
    path = base.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    paths = []
    if path.endswith("/models"):
        paths.append((path, format))
    elif re.search(r"/v\d+$", path):
        paths.append((path + "/models", format))
    else:
        suffixes = ("/v1/models", "/models") if format == "anthropic" else ("/models", "/v1/models")
        paths.extend((path + suffix, format) for suffix in suffixes)
    if format == "anthropic":
        for suffix in ("/api/anthropic", "/anthropic", "/coding"):
            if path.endswith(suffix):
                parent = path[:-len(suffix)]
                paths.extend((parent + ending, "openai") for ending in ("/models", "/v1/models"))
                break
    return list(dict.fromkeys((urlunsplit(base._replace(path=p, query="", fragment="")), fmt) for p, fmt in paths))


def _safe_text(value, key):
    return (value if isinstance(value, str) and 0 < len(value) <= 256
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and key not in value else "")


def list_models(*, base_url, api_key, format="openai", models_url="", anthropic_version="2023-06-01", opener=None):
    if format not in {"openai", "anthropic"} or not api_key or any(c in api_key for c in "\r\n"):
        raise ValueError("Invalid protocol or API key")
    opener = opener or urllib.request.build_opener(NoRedirect())
    deadline = time.monotonic() + 15
    for endpoint, protocol in endpoints(base_url, format, models_url):
        headers = {"Accept": "application/json"}
        if protocol == "anthropic":
            headers.update({"x-api-key": api_key, "anthropic-version": anthropic_version})
        else:
            headers["Authorization"] = "Bearer " + api_key
        models, cursors, cursor = {}, set(), None
        for page in range(4):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelDiscoveryError("timeout", "获取模型列表超时，可重试或手动输入模型名。")
            url = endpoint + ("?" + urlencode({"after_id": cursor}) if cursor else "")
            try:
                with opener.open(urllib.request.Request(url, headers=headers), timeout=min(5, remaining)) as response:
                    content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ModelDiscoveryError("too_large", "模型列表响应过大，请使用更具体的模型列表地址。")
                payload = json.loads(content)
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if status in {404, 405} and page == 0:
                    break
                code, message = {
                    401: ("unauthorized", "API Key 无效或已过期（HTTP 401）。"),
                    403: ("forbidden", "此凭据无权读取模型列表（HTTP 403），仍可手动填写模型名。"),
                    429: ("rate_limited", "模型列表接口限流（HTTP 429），请稍后重试。"),
                }.get(status, ("http_error", f"模型列表接口返回 HTTP {status}，可稍后重试或手动填写。"))
                if 300 <= status < 400:
                    code, message = "redirect", "模型列表接口发生重定向；为保护 API Key，未跟随跳转。请填写最终地址。"
                raise ModelDiscoveryError(code, message) from None
            except (TimeoutError, urllib.error.URLError, OSError):
                raise ModelDiscoveryError("connection", "模型列表连接失败或超时，请检查地址与网络。") from None
            except (ValueError, UnicodeError):
                raise ModelDiscoveryError("invalid_response", "接口未返回有效的模型列表 JSON，仍可手动填写模型名。") from None
            entries = payload.get("data", payload.get("models")) if isinstance(payload, dict) else payload
            if not isinstance(entries, list):
                raise ModelDiscoveryError("invalid_response", "接口响应不包含模型列表，仍可手动填写模型名。")
            for entry in entries:
                if isinstance(entry, str):
                    entry = {"id": entry}
                if not isinstance(entry, dict):
                    continue
                model_id = _safe_text(entry.get("id") or entry.get("slug"), api_key)
                if not model_id or model_id.strip() != model_id or any(c.isspace() for c in model_id):
                    continue
                models[model_id] = {"id": model_id, "name": _safe_text(entry.get("display_name") or entry.get("name"), api_key)}
                if len(models) >= 2000:
                    return {"models": sorted(models.values(), key=lambda item: item["id"]), "truncated": True}
            has_more = isinstance(payload, dict) and payload.get("has_more") is True
            if not has_more:
                if entries and not models:
                    raise ModelDiscoveryError("invalid_response", "响应中没有有效的模型标识，可手动填写模型名。")
                return {"models": sorted(models.values(), key=lambda item: item["id"]), "truncated": False}
            cursor = _safe_text(payload.get("last_id"), api_key)
            if page == 3 or not cursor or cursor in cursors:
                return {"models": sorted(models.values(), key=lambda item: item["id"]), "truncated": True}
            cursors.add(cursor)
    raise ModelDiscoveryError("unsupported", "该地址未提供模型列表接口（HTTP 404/405）。可指定模型列表地址，或手动填写模型名。")
