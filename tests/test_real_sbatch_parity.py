"""Native comparisons use isolated roots and never cancel or dispatch messages."""

import os
import re
import sqlite3
import subprocess
import sys
import time

import pytest

from real_slurm_support import first_available_partition, probe_real_slurm
from test_real_slurm_smoke import _real_workdir


pytestmark = pytest.mark.real_slurm


@pytest.fixture
def real_env(monkeypatch):
    workdir = _real_workdir()
    root = workdir / '.cslurm'
    root.mkdir()
    monkeypatch.chdir(workdir)
    for name, value in {
        'CSLURM_ROOT': str(root), 'CSLURM_AI_AUTO_SUMMARY': 'false',
        'CSLURM_STATIC_ANALYSIS': 'false', 'CSLURM_NOTIFICATION_ENABLED': 'false',
        'CSLURM_NOTIFICATION_AUTO_DISPATCH': 'false', 'CSLURM_WORKER_AUTOSTART': 'false',
    }.items():
        monkeypatch.setenv(name, value)
    native = probe_real_slurm().commands['sbatch']
    monkeypatch.setenv('CSLURM_SBATCH', native)
    return workdir, root, native


def _run(command, args, **kwargs):
    return subprocess.run([*command, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=180, **kwargs)


@pytest.mark.parametrize('args', [['--help'], ['--version'], ['--usage'],
                                 ['--not-a-slurm-option'], ['--wrap']])
def test_real_informational_and_invalid_arguments_match(real_env, args):
    _, _, native = real_env
    expected = _run([native], args)
    actual = _run([sys.executable, '-m', 'cslurm.cli.csbatch'], args)
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        expected.returncode, expected.stdout, expected.stderr)


def test_real_test_only_has_no_job_id_and_is_success(real_env):
    _, root, native = real_env
    args = ['--test-only', '--parsable', '-N1', '-n1', '-t00:01:00', '--wrap=exit 0']
    partition = first_available_partition()
    if partition:
        args.extend(['-p', partition])
    expected = _run([native], args)
    actual = _run([sys.executable, '-m', 'cslurm.cli.csbatch'], args)
    assert actual.returncode == expected.returncode == 0
    assert actual.stdout == expected.stdout == b''
    assert b'Traceback' not in actual.stderr
    assert not (root / 'db.sqlite').exists()


@pytest.mark.parametrize('mode,exit_code', [('file', 0), ('stdin', 0), ('wrap', 7)])
def test_real_submission_forms_and_wait_status(real_env, mode, exit_code):
    workdir, root, _ = real_env
    args = ['--parsable', '--wait', '-N1', '-n1', '-c1', '-t00:01:00', '--mem=32M',
            '--output=parity-%j.out', '--job-name=cslurm-parity']
    partition = first_available_partition()
    if partition:
        args.extend(['-p', partition])
    for option, variable in [('account', 'CSLURM_REAL_SLURM_ACCOUNT'), ('qos', 'CSLURM_REAL_SLURM_QOS')]:
        if os.environ.get(variable):
            args.extend(['--' + option, os.environ[variable]])
    script = '#!/bin/sh\n\n# directives after blank lines\n#SBATCH --job-name=script-name\nprintf "parity-ok\\n"\n'
    stdin = None
    if mode == 'file':
        path = workdir / 'job.slurm'
        path.write_text(script)
        args.append(str(path))
    elif mode == 'stdin':
        stdin = script.encode()
    else:
        args.extend(['--wrap', 'echo parity-ok; exit 7'])
    result = _run([sys.executable, '-m', 'cslurm.cli.csbatch'], args, input=stdin)
    assert result.returncode == exit_code, result.stderr
    match = re.fullmatch(rb'(\d+)\n', result.stdout)
    assert match, result.stdout
    job_id = match[1].decode()
    print(f'parity mode={mode} job_id={job_id} exit={result.returncode} root={root}')
    assert 'parity-ok' in (workdir / f'parity-{job_id}.out').read_text()
    from cslurm.slurm.submissions import ingest
    from cslurm.slurm.tracker import track_once
    assert ingest() == 1
    deadline = time.monotonic() + 60
    while True:
        track_once()
        with sqlite3.connect(root / 'db.sqlite') as conn:
            state = conn.execute('select state from jobs where job_id=?', (job_id,)).fetchone()[0]
        if state == ('FAILED' if exit_code else 'COMPLETED'):
            break
        assert time.monotonic() < deadline, (job_id, state)
        time.sleep(2)
