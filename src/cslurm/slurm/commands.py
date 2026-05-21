import subprocess

from cslurm.config import command_path


def run_slurm_command(name: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [command_path(name), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
