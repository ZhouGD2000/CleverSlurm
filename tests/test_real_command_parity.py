"""Real scancel is restricted to information queries; never cancel any job."""

import os
import sqlite3
import subprocess
import sys

import pytest

from real_slurm_support import first_available_partition, probe_real_slurm
from test_real_slurm_smoke import _real_workdir


pytestmark = pytest.mark.real_slurm


@pytest.fixture
def command_env(monkeypatch):
    workdir = _real_workdir()
    root = workdir / '.cslurm'
    root.mkdir()
    monkeypatch.chdir(workdir)
    for key in list(os.environ):
        if key.startswith(('SCANCEL_', 'SRUN_')) or key in {
            'SLURM_CLUSTERS', 'SLURM_JOB_ID', 'SLURM_JOBID',
            'SLURM_ARRAY_JOB_ID', 'SLURM_ARRAY_TASK_ID',
        }:
            monkeypatch.delenv(key)
    for key, value in {
        'CSLURM_ROOT': str(root), 'CSLURM_AI_AUTO_SUMMARY': 'false',
        'CSLURM_STATIC_ANALYSIS': 'false', 'CSLURM_NOTIFICATION_ENABLED': 'false',
        'CSLURM_NOTIFICATION_AUTO_DISPATCH': 'false', 'CSLURM_WORKER_AUTOSTART': 'false',
        'CSLURM_TRACK_COMMANDS': 'true',
    }.items():
        monkeypatch.setenv(key, value)
    commands = probe_real_slurm().commands
    for name in ('srun', 'scancel'):
        monkeypatch.setenv('CSLURM_' + name.upper(), commands[name])
    return root, commands


@pytest.mark.parametrize('name', ['srun', 'scancel'])
@pytest.mark.parametrize('flag', ['--help', '--usage', '--version'])
def test_real_information_matches_native(command_env, name, flag):
    root, commands = command_env
    expected = subprocess.run([commands[name], flag], capture_output=True, timeout=15)
    actual = subprocess.run([sys.executable, '-m', 'cslurm.cli.c' + name, flag],
                            capture_output=True, timeout=15)
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        expected.returncode, expected.stdout, expected.stderr)
    assert not (root / 'db.sqlite').exists()
    assert not (root / 'spool').exists()


@pytest.mark.parametrize('exit_code', [0, 7])
def test_real_srun_streams_exit_and_receipt(command_env, exit_code):
    root, _ = command_env
    args = ['-N1', '-n1', '-c1', '-t00:01:00', '--mem=32M', '--job-name=cslurm-srun-parity']
    partition = first_available_partition()
    if partition:
        args.extend(['-p', partition])
    for key in ('ACCOUNT', 'QOS'):
        if os.environ.get('CSLURM_REAL_SLURM_' + key):
            args.extend(['--' + key.lower(), os.environ['CSLURM_REAL_SLURM_' + key]])
    args.extend(['/bin/sh', '-c', 'read value; printf "stdin=%s job=%s\\n" "$value" "$SLURM_JOB_ID"; '
                 f'printf "stderr-ok\\n" >&2; exit {exit_code}'])
    result = subprocess.run([sys.executable, '-m', 'cslurm.cli.csrun', *args],
                            input=b'hello\n', capture_output=True, timeout=180)
    assert result.returncode == exit_code, result.stderr
    assert result.stdout.startswith(b'stdin=hello job=')
    assert b'stderr-ok\n' in result.stderr
    assert b'Traceback' not in result.stderr
    assert not (root / 'db.sqlite').exists()
    from cslurm.runtime.receipts import ingest_commands
    assert ingest_commands() == 1
    with sqlite3.connect(root / 'db.sqlite') as conn:
        assert conn.execute('select state,exit_code from jobs').fetchone() == (
            'FAILED' if exit_code else 'COMPLETED', f'{exit_code}:0')
        assert conn.execute('select count(*) from notifications').fetchone()[0] == 0
    print(f'srun parity: {result.stdout.decode().strip()} exit={exit_code} root={root}')
