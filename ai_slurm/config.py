import os
from pathlib import Path


def root_dir() -> Path:
    return Path(os.environ.get("AI_SLURM_ROOT", "~/.ai-slurm")).expanduser()


def db_path() -> Path:
    return root_dir() / "db.sqlite"


def command_path(name: str) -> str:
    env_name = f"AI_SLURM_{name.upper()}"
    return os.environ.get(env_name, name)
