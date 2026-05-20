import os
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"


@pytest.fixture(autouse=True)
def source_tree_on_pythonpath(monkeypatch):
    current = os.environ.get("PYTHONPATH")
    value = str(SRC_DIR) if not current else f"{SRC_DIR}{os.pathsep}{current}"
    monkeypatch.setenv("PYTHONPATH", value)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".ai-slurm"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AI_SLURM_ROOT", str(root))
    return root


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bin_dir


def write_executable(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o755)
    return path
