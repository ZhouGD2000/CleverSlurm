import os
import json
import ast
from pathlib import Path


def root_dir() -> Path:
    return Path(os.environ.get("AI_SLURM_ROOT", "~/.ai-slurm")).expanduser()


def db_path() -> Path:
    return root_dir() / "db.sqlite"


def command_path(name: str) -> str:
    env_name = f"AI_SLURM_{name.upper()}"
    return os.environ.get(env_name, name)


def config_path() -> Path:
    return root_dir() / "config.toml"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    config: dict[str, dict[str, str]] = {}
    section: str | None = None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            config.setdefault(section, {})
            continue
        if section and "=" in line:
            key, value = line.split("=", 1)
            config[section][key.strip()] = _parse_config_value(value.strip())
    return config


def _parse_config_value(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
        if isinstance(parsed, str):
            return parsed
    return value


def ai_provider() -> str:
    return (
        os.environ.get("AI_SLURM_AI_PROVIDER")
        or load_config().get("ai", {}).get("provider")
        or "openai-compatible"
    )


def ai_api_key_env() -> str | None:
    return os.environ.get("AI_SLURM_AI_API_KEY_ENV") or load_config().get("ai", {}).get("api_key_env")


def ai_api_key() -> str | None:
    env_name = ai_api_key_env()
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return os.environ.get("AI_SLURM_AI_API_KEY") or load_config().get("ai", {}).get("api_key")


def ai_base_url() -> str | None:
    return (
        os.environ.get("AI_SLURM_AI_BASE_URL")
        or load_config().get("ai", {}).get("base_url")
    )


def ai_model() -> str | None:
    return (
        os.environ.get("AI_SLURM_AI_MODEL")
        or load_config().get("ai", {}).get("model")
    )


def ai_max_tokens() -> int:
    value = os.environ.get("AI_SLURM_AI_MAX_TOKENS") or load_config().get("ai", {}).get("max_tokens")
    if value is None:
        return 512
    return int(value)


def ai_enable_thinking() -> bool | None:
    value = os.environ.get("AI_SLURM_AI_ENABLE_THINKING") or load_config().get("ai", {}).get("enable_thinking")
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def ai_temperature() -> float | None:
    value = os.environ.get("AI_SLURM_AI_TEMPERATURE") or load_config().get("ai", {}).get("temperature")
    if value is None:
        return 0.2
    return float(value)


def ai_top_p() -> float | None:
    value = os.environ.get("AI_SLURM_AI_TOP_P") or load_config().get("ai", {}).get("top_p")
    if value is None:
        return 0.7
    return float(value)


def ai_anthropic_version() -> str:
    return (
        os.environ.get("AI_SLURM_AI_ANTHROPIC_VERSION")
        or load_config().get("ai", {}).get("anthropic_version")
        or "2023-06-01"
    )


def ai_extra_body() -> dict:
    value = os.environ.get("AI_SLURM_AI_EXTRA_BODY_JSON") or load_config().get("ai", {}).get("extra_body_json")
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("AI extra_body_json must decode to a JSON object")
    return parsed


def ai_response_format() -> str | None:
    value = os.environ.get("AI_SLURM_AI_RESPONSE_FORMAT") or load_config().get("ai", {}).get("response_format")
    if value is None:
        return "json_object"
    normalized = value.strip().lower()
    if normalized in {"", "none", "false", "off"}:
        return None
    return normalized


def ai_auto_summary_enabled() -> bool:
    value = os.environ.get("AI_SLURM_AI_AUTO_SUMMARY") or load_config().get("ai", {}).get("auto_summary")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "on"}


def _bool_config(env_name: str, section: str, key: str, default: bool) -> bool:
    value = os.environ.get(env_name) or load_config().get(section, {}).get(key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _looks_like_env_var_name(value: str) -> bool:
    return bool(value) and value.upper() == value and value.replace("_", "").isalnum() and not value[0].isdigit()


def feishu_webhook_url() -> str | None:
    section = load_config().get("notification.feishu", {})
    env_name = section.get("webhook_url_env") or "AI_SLURM_FEISHU_WEBHOOK"
    value = os.environ.get(env_name) or section.get("webhook_url")
    if value:
        return value
    if section.get("webhook_url_env") and _looks_like_url(section["webhook_url_env"]):
        return section["webhook_url_env"]
    return None


def feishu_secret() -> str | None:
    section = load_config().get("notification.feishu", {})
    env_name = section.get("secret_env") or "AI_SLURM_FEISHU_SECRET"
    value = os.environ.get(env_name) or section.get("secret")
    if value:
        return value
    if section.get("secret_env") and not _looks_like_env_var_name(section["secret_env"]):
        return section["secret_env"]
    return None


def feishu_message_format() -> str:
    return (
        os.environ.get("AI_SLURM_FEISHU_MESSAGE_FORMAT")
        or load_config().get("notification.feishu", {}).get("message_format")
        or "card"
    )


def notification_enabled() -> bool:
    default = feishu_webhook_url() is not None
    return _bool_config("AI_SLURM_NOTIFICATION_ENABLED", "notification", "enabled", default)


def notification_auto_dispatch_enabled() -> bool:
    return _bool_config("AI_SLURM_NOTIFICATION_AUTO_DISPATCH", "notification", "auto_dispatch", True)


def notification_batch_window_minutes() -> int:
    value = (
        os.environ.get("AI_SLURM_NOTIFICATION_BATCH_WINDOW_MINUTES")
        or load_config().get("notification.feishu", {}).get("batch_window_minutes")
        or load_config().get("notification", {}).get("batch_window_minutes")
    )
    if value is None:
        return 30
    return int(value)


def notification_ai_analysis_enabled() -> bool:
    return _bool_config("AI_SLURM_NOTIFICATION_AI_ANALYSIS", "notification", "ai_analysis", False)
