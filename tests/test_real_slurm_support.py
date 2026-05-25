import subprocess

from real_slurm_support import REQUIRED_SLURM_COMMANDS, probe_real_slurm


def test_probe_real_slurm_skips_when_disabled_by_env():
    probe = probe_real_slurm(
        command_resolver=lambda command, environ: f"/usr/bin/{command}",
        run=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ok", ""),
        environ={"CSLURM_RUN_REAL_SLURM": "0"},
    )

    assert probe.available is False
    assert probe.reason == "disabled by CSLURM_RUN_REAL_SLURM=0"


def test_probe_real_slurm_skips_when_required_command_is_missing():
    probe = probe_real_slurm(
        command_resolver=lambda command, environ: None if command == "sacct" else f"/usr/bin/{command}",
        run=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ok", ""),
        environ={},
    )

    assert probe.available is False
    assert probe.reason == "missing Slurm command: sacct"


def test_probe_real_slurm_is_available_when_all_required_commands_answer_version():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"{argv[0]} 23.11", "")

    probe = probe_real_slurm(
        command_resolver=lambda command, environ: f"/usr/bin/{command}",
        run=fake_run,
        environ={},
    )

    assert probe.available is True
    assert probe.reason is None
    assert set(probe.commands) == set(REQUIRED_SLURM_COMMANDS)
    assert calls == [[f"/usr/bin/{command}", "--version"] for command in REQUIRED_SLURM_COMMANDS]


def test_probe_real_slurm_skips_when_version_command_fails():
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "not configured")

    probe = probe_real_slurm(
        command_resolver=lambda command, environ: f"/usr/bin/{command}",
        run=fake_run,
        environ={},
    )

    assert probe.available is False
    assert probe.reason == "sbatch --version failed: not configured"
