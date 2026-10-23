"""Private configuration editing; no job database or notification queue writes."""

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cslurm.config import config_path, parse_config, _looks_like_env_var_name, _looks_like_url


class SettingsConflict(Exception):
    pass


def field(key, label, *, secret=False, choices=None, bounds=None, default="", kind="text"):
    return dict(key=key, label=label, secret=secret, choices=choices, bounds=bounds, default=default, kind=kind)


FIELDS = {
    "ai": [
        field("format", "接口格式", choices=["openai", "anthropic"], default="openai"),
        field("base_url", "Base URL"), field("model", "模型名称"),
        field("models_url", "模型列表地址（可选）"),
        field("api_key", "API Key", secret=True), field("api_key_env", "API Key 环境变量"),
        field("fallback_models", "备用模型（逗号分隔）"),
        field("timeout_seconds", "请求超时（秒）", bounds=[1, 600], default="60"),
        field("max_tokens", "最大输出 Tokens", bounds=[1, 131072], default="512"),
        field("request_retries", "重试次数", bounds=[0, 5], default="1"),
        field("enable_thinking", "Thinking", choices=["", "true", "false"]),
        field("response_format", "响应格式", choices=["", "none", "json_object"]),
        field("temperature", "Temperature", bounds=[0, 2], default="0.2", kind="float"),
        field("top_p", "Top P", bounds=[0, 1], default="0.7", kind="float"),
        field("anthropic_version", "Anthropic API 版本", default="2023-06-01"),
        field("extra_body_json", "附加请求 JSON", secret=True),
        field("auto_summary", "提交后自动摘要", kind="bool", default="true"),
    ],
    "feishu": [
        field("webhook_url", "Webhook URL", secret=True),
        field("secret", "签名 Secret", secret=True),
        field("webhook_url_env", "Webhook 环境变量"), field("secret_env", "Secret 环境变量"),
        field("message_format", "消息格式", choices=["card", "text"], default="card"),
        field("batch_window_minutes", "批量通知窗口（分钟）", bounds=[0, 10080]),
        field("immediate_group_threshold", "即时通知合并阈值", bounds=[1, 10000]),
    ],
    "notification": [
        field("enabled", "启用通知", choices=["", "true", "false"]),
        field("auto_dispatch", "自动发送通知", kind="bool", default="true"),
        field("ai_analysis", "AI 结果分析", kind="bool", default="false"),
        field("batch_window_minutes", "默认批量窗口（分钟）", bounds=[0, 10080], default="30"),
        field("immediate_group_threshold", "默认即时合并阈值", bounds=[1, 10000], default="10"),
    ],
}
SECTIONS = {"ai": "ai", "feishu": "notification.feishu", "notification": "notification"}


def _read():
    path = config_path()
    raw = path.read_bytes() if path.exists() else b""
    return raw, parse_config(raw.decode()), hashlib.sha256(raw).hexdigest()


def _section(kind, profile_id):
    if kind not in FIELDS or not isinstance(profile_id, str):
        raise ValueError("Unknown configuration type")
    if profile_id == "current":
        return SECTIONS[kind]
    if kind == "notification" or not re.fullmatch(r"[a-f0-9]{32}", profile_id):
        raise ValueError("Invalid profile")
    return f"web.{kind}.{profile_id}"


def _profile(config, section, kind):
    values = dict(config.get(section, {}))
    # Older hand-written files can put literal credentials in *_env fields.
    # Keep those values usable, but never expose them as variable names.
    if kind == "feishu":
        webhook = values.get("webhook_url_env", "")
        if _looks_like_url(webhook):
            values.setdefault("webhook_url", webhook)
            values.pop("webhook_url_env", None)
        secret = values.get("secret_env", "")
        if secret and not _looks_like_env_var_name(secret):
            values.setdefault("secret", secret)
            values.pop("secret_env", None)
    return values


def snapshot():
    _, config, revision = _read()
    groups = {}
    for kind, fields in FIELDS.items():
        profiles = []
        sections = [("current", SECTIONS[kind])]
        prefix = f"web.{kind}."
        sections += [(s[len(prefix):], s) for s in config if s.startswith(prefix)
                     and re.fullmatch(r"[a-f0-9]{32}", s[len(prefix):])]
        for profile_id, section in sections:
            values = _profile(config, section, kind)
            profiles.append({
                "id": profile_id, "name": values.get("name", "当前配置"),
                "values": {f["key"]: values.get(f["key"], "") for f in fields if not f["secret"]},
                "secrets": {f["key"]: bool(values.get(f["key"])) for f in fields if f["secret"]},
            })
        groups[kind] = profiles
    # Only environment variable names are exposed, never their contents.
    overrides = sorted(key for key, value in os.environ.items() if value and
                       key.startswith(("CSLURM_AI_", "CSLURM_FEISHU_", "CSLURM_NOTIFICATION_")))
    return {"revision": revision, "groups": groups, "fields": FIELDS, "environment_overrides": overrides}


def _validate(values, kind, previous):
    if not isinstance(values, dict) or set(values) - {f["key"] for f in FIELDS[kind]}:
        raise ValueError("Unknown configuration fields")
    result = {}
    for f in FIELDS[kind]:
        key = f["key"]
        value = values.get(key, previous.get(key, ""))
        if value is None and f["secret"]:
            value = previous.get(key, "")
        if not isinstance(value, str) or len(value) > 8192 or "\n" in value or "\r" in value:
            raise ValueError("Invalid configuration value")
        value = value.strip()
        if value:
            if f["choices"] and value not in f["choices"]:
                raise ValueError("Invalid option")
            if f["kind"] == "bool" and value not in {"true", "false"}:
                raise ValueError("Invalid boolean")
            if f["bounds"]:
                number = float(value) if f["kind"] == "float" else int(value)
                if not math.isfinite(number) or not f["bounds"][0] <= number <= f["bounds"][1]:
                    raise ValueError("Number outside allowed range")
            if key.endswith("_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ValueError("Expected an environment variable name")
            if key in {"base_url", "webhook_url", "models_url"}:
                url = urlsplit(value)
                if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.fragment:
                    raise ValueError("Invalid endpoint URL")
                if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                    raise ValueError("Remote endpoints must use HTTPS")
            if key == "extra_body_json" and not isinstance(json.loads(value), dict):
                raise ValueError("Expected a JSON object")
        result[key] = value
    return result


def _replace_section(text, section, values):
    # Retain unrelated sections/comments; only managed keys in the target change.
    output, inside, found = [], False, False
    keys = set(values)
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped[1:-1].strip() == section
            if inside:
                if found:
                    raise ValueError("Duplicate configuration section")
                found = True
                output.append(line if line.endswith("\n") else line + "\n")
                output.extend(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in values.items() if value)
                continue
        if inside and not stripped.startswith("#") and "=" in stripped and stripped.split("=", 1)[0].strip() in keys:
            continue
        output.append(line)
    if not found:
        output.append("\n[" + section + "]\n")
        output.extend(f'{key} = {json.dumps(value, ensure_ascii=False)}\n' for key, value in values.items() if value)
    return "".join(output)


def save(body):
    kind, profile_id = body.get("kind"), body.get("id", "current")
    if kind not in FIELDS:
        raise ValueError("Unknown configuration type")
    if profile_id == "new":
        profile_id = uuid.uuid4().hex
    section = _section(kind, profile_id)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / "config.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw, config, revision = _read()
        if body.get("revision") != revision:
            raise SettingsConflict("配置已被其他窗口或进程修改，请重新载入后再保存。")
        previous = _profile(config, section, kind)
        action = body.get("action", "save")
        if action == "duplicate":
            if kind == "notification":
                raise ValueError("Notification policy is global")
            values = _validate({}, kind, previous)
            profile_id = uuid.uuid4().hex
            section = _section(kind, profile_id)
            values["name"] = (previous.get("name") or previous.get("model") or "飞书配置")[:90] + " 副本"
        elif action == "activate":
            if profile_id == "current" or section not in config:
                raise ValueError("Choose a saved profile")
            values = _validate({}, kind, previous)
            if kind == "ai" and (not values["base_url"] or not values["model"] or not (values["api_key"] or values["api_key_env"])):
                raise ValueError("Model profile is incomplete")
            if kind == "feishu" and not (values["webhook_url"] or values["webhook_url_env"]):
                raise ValueError("Feishu profile is incomplete")
            section = SECTIONS[kind]
        elif action == "save":
            values = _validate(body.get("values", {}), kind, previous)
            if profile_id != "current":
                name = body.get("name", previous.get("name", ""))
                if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(c in name for c in "\r\n"):
                    raise ValueError("A profile name is required")
                values["name"] = name.strip()
        else:
            raise ValueError("Unknown settings action")
        updated = _replace_section(raw.decode(), section, values).encode()
        # Backups and replacements are private and fsynced before publishing.
        backups = path.parent / "config-backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        if raw:
            fd, _ = tempfile.mkstemp(prefix="config-", suffix=".toml", dir=backups)
            with os.fdopen(fd, "wb") as backup:
                backup.write(raw)
                backup.flush()
                os.fsync(backup.fileno())
        fd, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            if _read()[2] != revision:
                raise SettingsConflict("配置在保存期间发生变化，请重新载入。")
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {**snapshot(), "saved_id": profile_id}


def test_connection(body):
    kind, profile_id = body.get("kind"), body.get("id", "current")
    section = _section(kind, profile_id)
    _, config, revision = _read()
    if body.get("revision") != revision:
        raise SettingsConflict("配置已修改，请重新载入后测试。")
    values = _validate({}, kind, _profile(config, section, kind))
    if kind == "ai":
        from cslurm.ai.client import ModelClient
        key = os.environ.get(values["api_key_env"]) if values["api_key_env"] else None
        key = key or values["api_key"]
        if not key or not values["base_url"] or not values["model"]:
            raise ValueError("Missing model connection settings")
        # Explicit values prevent accidentally testing another active provider.
        client = ModelClient(api_key=key, base_url=values["base_url"], model=values["model"],
                             format=values["format"] or "openai", max_tokens=64,
                             temperature=0.2, top_p=0.7, timeout=10, request_retries=0,
                             fallback_models=[], extra_body=json.loads(values["extra_body_json"] or "{}"),
                             response_format=None, anthropic_version=values["anthropic_version"] or "2023-06-01")
        client.enable_thinking = None if not values["enable_thinking"] else values["enable_thinking"] == "true"
        client.chat_raw([{"role": "user", "content": "Reply with OK."}])
        return {"message": "模型连接成功（短文本请求）。"}
    if kind == "feishu" and body.get("confirm_send") is True:
        from cslurm.notify.feishu import build_text_payload, build_card_payload, send_payload
        webhook = os.environ.get(values["webhook_url_env"] or "CSLURM_FEISHU_WEBHOOK") or values["webhook_url"]
        secret = os.environ.get(values["secret_env"] or "CSLURM_FEISHU_SECRET") or values["secret"]
        if not webhook:
            raise ValueError("Missing webhook")
        text = "[CleverSlurm] 网页通知渠道连接测试。此消息不是任务通知。"
        payload = build_text_payload(text, secret=secret) if values["message_format"] == "text" else build_card_payload("CleverSlurm 连接测试", text, secret=secret)
        send_payload(webhook, payload, timeout=10, attempts=1)
        return {"message": "飞书已接受测试消息。"}
    raise ValueError("Explicit confirmation is required for a Feishu test")


def fetch_models(body):
    from cslurm.ai.models import list_models
    if body.get("kind") != "ai":
        raise ValueError("Model discovery is only available for AI profiles")
    profile_id = body.get("id", "current")
    section = None if profile_id == "new" else _section("ai", profile_id)
    _, config, revision = _read()
    if body.get("revision") != revision:
        raise SettingsConflict("配置已修改，请重新载入后获取模型列表。")
    allowed = {"format", "base_url", "models_url", "api_key", "api_key_env", "anthropic_version"}
    draft = body.get("values", {})
    if not isinstance(draft, dict) or set(draft) - allowed:
        raise ValueError("Unexpected discovery fields")
    previous = {k: v for k, v in config.get(section, {}).items() if k in allowed}
    values = _validate(draft, "ai", previous)
    key = (os.environ.get(values["api_key_env"]) if values["api_key_env"] else None) or values["api_key"]
    if not key or not values["base_url"]:
        raise ValueError("Base URL and API key are required")
    return list_models(base_url=values["base_url"], api_key=key, format=values["format"] or "openai",
                       models_url=values["models_url"], anthropic_version=values["anthropic_version"] or "2023-06-01")
