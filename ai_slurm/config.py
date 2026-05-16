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
