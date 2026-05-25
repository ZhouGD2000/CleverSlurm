import os
from pathlib import Path

import pytest

from real_slurm_support import probe_real_slurm


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
    root = home / ".cslurm"
    home.mkdir()
    root.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CSLURM_ROOT", str(root))
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_slurm: tests that use a real Slurm installation and may submit one short smoke job",
    )


def pytest_collection_modifyitems(config, items):
    real_items = [item for item in items if "real_slurm" in item.keywords]
    if not real_items:
        return
    probe = probe_real_slurm()
    if probe.available:
        return
    marker = pytest.mark.skip(reason=f"real Slurm unavailable: {probe.reason}")
    for item in real_items:
        item.add_marker(marker)
