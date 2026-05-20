import os
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
            config[section][key.strip()] = value.strip().strip('"').strip("'")
    return config


def ai_api_key() -> str | None:
    return (
        os.environ.get("AI_SLURM_AI_API_KEY")
        or os.environ.get("SILICONFLOW_API_KEY")
        or load_config().get("ai", {}).get("api_key")
    )


def ai_model() -> str:
    return (
        os.environ.get("AI_SLURM_AI_MODEL")
        or load_config().get("ai", {}).get("model")
        or "Qwen/Qwen3.5-4B"
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


def feishu_webhook_url() -> str | None:
    section = load_config().get("notification.feishu", {})
    env_name = section.get("webhook_url_env") or "AI_SLURM_FEISHU_WEBHOOK"
    return os.environ.get(env_name) or section.get("webhook_url")


def feishu_secret() -> str | None:
    section = load_config().get("notification.feishu", {})
    env_name = section.get("secret_env") or "AI_SLURM_FEISHU_SECRET"
    return os.environ.get(env_name) or section.get("secret")


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
