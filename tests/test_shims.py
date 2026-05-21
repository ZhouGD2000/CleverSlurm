import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def test_shim_install_creates_real_sbatch_command_for_subprocess(tmp_path, monkeypatch):
    from cslurm import shims

    install_bin = tmp_path / "install-bin"
    real_bin = tmp_path / "real-bin"
    install_bin.mkdir()
    real_bin.mkdir()
    _write_executable(install_bin / "csbatch", "#!/bin/sh\nprintf 'csbatch:%s:%s\\n' \"$CSLURM_SBATCH\" \"$*\"\n")
    _write_executable(install_bin / "csrun", "#!/bin/sh\nexit 0\n")
    _write_executable(install_bin / "cscancel", "#!/bin/sh\nexit 0\n")
    _write_executable(real_bin / "sbatch", "#!/bin/sh\nexit 0\n")
    _write_executable(real_bin / "srun", "#!/bin/sh\nexit 0\n")
    _write_executable(real_bin / "scancel", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{install_bin}{os.pathsep}{real_bin}")

    installed = shims.install(bin_dir=install_bin)

    assert {item.slurm_command for item in installed} == {"sbatch", "srun", "scancel"}
    assert shims.MANAGED_MARKER in (install_bin / "sbatch").read_text()
    result = subprocess.run(
        ["sbatch", "job.slurm"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert result.stdout.strip() == f"csbatch:{real_bin / 'sbatch'}:job.slurm"


def test_shim_install_refuses_unmanaged_existing_command(tmp_path, monkeypatch):
    from cslurm import shims

    install_bin = tmp_path / "install-bin"
    real_bin = tmp_path / "real-bin"
    install_bin.mkdir()
    real_bin.mkdir()
    _write_executable(install_bin / "csbatch", "#!/bin/sh\nexit 0\n")
    _write_executable(install_bin / "csrun", "#!/bin/sh\nexit 0\n")
    _write_executable(install_bin / "cscancel", "#!/bin/sh\nexit 0\n")
    _write_executable(install_bin / "sbatch", "#!/bin/sh\nexit 99\n")
    _write_executable(real_bin / "sbatch", "#!/bin/sh\nexit 0\n")
    _write_executable(real_bin / "srun", "#!/bin/sh\nexit 0\n")
    _write_executable(real_bin / "scancel", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{install_bin}{os.pathsep}{real_bin}")

    try:
        shims.install(bin_dir=install_bin)
    except RuntimeError as exc:
        assert "Refusing to overwrite unmanaged command" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_shim_remove_only_removes_managed_wrappers(tmp_path, monkeypatch):
    from cslurm import shims

    install_bin = tmp_path / "install-bin"
    real_bin = tmp_path / "real-bin"
    install_bin.mkdir()
    real_bin.mkdir()
    for command in ["csbatch", "csrun", "cscancel"]:
        _write_executable(install_bin / command, "#!/bin/sh\nexit 0\n")
    for command in ["sbatch", "srun", "scancel"]:
        _write_executable(real_bin / command, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{install_bin}{os.pathsep}{real_bin}")
    shims.install(bin_dir=install_bin)
    (install_bin / "srun").write_text("#!/bin/sh\nexit 1\n")

    removed = shims.remove(bin_dir=install_bin)

    assert removed == [install_bin / "sbatch", install_bin / "scancel"]
    assert not (install_bin / "sbatch").exists()
    assert (install_bin / "srun").exists()


def test_cshim_status_cli(monkeypatch, capsys, tmp_path):
    from cslurm.cli.cshim import main
    from cslurm.shims import ShimStatus

    monkeypatch.setattr(
        "cslurm.cli.cshim.shims.status",
        lambda bin_dir=None: [
            ShimStatus(
                slurm_command="sbatch",
                target=tmp_path / "sbatch",
                installed=True,
                active_path=str(tmp_path / "sbatch"),
                real_path="/usr/bin/sbatch",
            )
        ],
    )
    monkeypatch.setattr("sys.argv", ["cshim", "status"])

    main()

    out = capsys.readouterr().out
    assert "sbatch\tinstalled" in out
    assert "real=/usr/bin/sbatch" in out
