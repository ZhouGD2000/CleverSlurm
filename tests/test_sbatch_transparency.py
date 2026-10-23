import json
import os
import signal
import sqlite3
import subprocess
import sys

import pytest

from conftest import write_executable


def invoke(args, **kwargs):
    return subprocess.run([sys.executable, '-m', 'cslurm.cli.csbatch', *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=10, **kwargs)


@pytest.mark.parametrize('args', [
    ['--help'], ['--version'], ['--test-only', '--wrap=true'],
    ['--quiet', '--parsable', '--wrap=true'],
    ['--wrap', 'echo ok', '--partition=CPU2', '--parsable'],
    ['-i', 'input.dat', 'job.slurm', '--help'],
    ['--acctg-freq', 'task=1', '--unknown-plugin', 'value', 'job.slurm'],
    ['-vvN1', ':', '-N2', 'job.slurm', 'arg with spaces'],
    ['--', '-job.slurm', ''], [],
])
def test_exact_argv_even_without_job_id(isolated_home, fake_bin, tmp_path, args):
    calls = tmp_path / 'calls.json'
    write_executable(fake_bin / 'sbatch',
                     f'#!{sys.executable}\nimport json,sys\n'
                     f'open({str(calls)!r}, "w").write(json.dumps(sys.argv[1:]))\n')
    result = invoke(args, input=b'#!/bin/sh\ntrue\n')
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == b''
    assert json.loads(calls.read_text()) == args


@pytest.mark.parametrize('code', [0, 7, 42])
def test_native_bytes_and_exit_status(isolated_home, fake_bin, code):
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport os\n'
                     f'os.write(1,b"12345;cluster-a\\n\\xff")\n'
                     f'os.write(2,b"native error\\xfe\\n")\nraise SystemExit({code})\n')
    result = invoke(['--wait', '--wrap=exit 7'])
    assert result.returncode == code
    assert result.stdout == b'12345;cluster-a\n\xff'
    assert result.stderr == b'native error\xfe\n'


def test_stdin_and_inherited_export_descriptor(isolated_home, fake_bin, tmp_path):
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport os,sys\n'
                     'os.write(1, sys.stdin.buffer.read())\n'
                     'os.write(2, os.read(int(sys.argv[1].split("=")[1]), 100))\n')
    envfile = tmp_path / 'export.bin'
    envfile.write_bytes(b'FOO=bar\0')
    with envfile.open('rb') as stream:
        result = invoke([f'--export-file={stream.fileno()}'], input=b'#!/bin/sh\nexit 0\n',
                        pass_fds=(stream.fileno(),))
    assert result.returncode == 0
    assert result.stdout == b'#!/bin/sh\nexit 0\n'
    assert result.stderr == b'FOO=bar\0'


def test_unwritable_root_does_not_break_success(fake_bin, tmp_path, monkeypatch):
    root = tmp_path / 'not-a-directory'
    root.write_text('preserve')
    monkeypatch.setenv('CSLURM_ROOT', str(root))
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    result = invoke(['--wrap=true'])
    assert result.returncode == 0
    assert result.stdout == b'Submitted batch job 12345\n'
    assert result.stderr == b''
    assert root.read_text() == 'preserve'


def test_cli_spools_without_waiting_for_locked_database(isolated_home, fake_bin):
    from cslurm.db import connect, init_db
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    with connect() as conn:
        init_db(conn)
        conn.execute('begin exclusive')
        result = invoke(['--wrap=true'])
    assert result.returncode == 0, result.stderr
    assert len(list((isolated_home / 'spool/sbatch').glob('*.json'))) == 1


def test_wait_failure_is_still_recorded_and_replay_keeps_terminal_state(isolated_home, fake_bin, monkeypatch):
    monkeypatch.setenv('CSLURM_AI_AUTO_SUMMARY', 'false')
    monkeypatch.setenv('CSLURM_STATIC_ANALYSIS', 'false')
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\nexit 7\n')
    result = invoke(['--wait', '--wrap=exit 7'])
    assert result.returncode == 7
    from cslurm.slurm.submissions import ingest
    receipts = list((isolated_home / 'spool/sbatch').glob('*.json'))
    original = receipts[0].read_bytes()
    assert ingest() == 1
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        conn.execute("update jobs set state='FAILED',summary_json='{}' where job_id='12345'")
    receipts[0].write_bytes(original)
    assert ingest() == 0
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        assert conn.execute('select state,summary_json from jobs').fetchone() == ('FAILED', '{}')
        assert conn.execute("select count(*) from job_events where event_type='SUBMITTED'").fetchone()[0] == 1


def test_tracking_off_executes_native_with_terminal_identity(isolated_home, fake_bin, monkeypatch):
    monkeypatch.setenv('CSLURM_TRACK_SUBMISSIONS', 'false')
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport os\n'
                     'print(os.getpid(), os.isatty(1))\n')
    process = subprocess.Popen([sys.executable, '-m', 'cslurm.cli.csbatch', '--version'], stdout=subprocess.PIPE)
    output, _ = process.communicate(timeout=10)
    assert output == f'{process.pid} False\n'.encode()
    assert not (isolated_home / 'db.sqlite').exists()


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGKILL])
def test_native_signal_exit_is_preserved(isolated_home, fake_bin, signum):
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport os\n'
                     f'os.kill(os.getpid(), {int(signum)})\n')
    result = invoke(['--wrap=true'])
    assert result.returncode == -signum
    assert result.stderr == b''


def test_native_reads_original_non_shell_script(isolated_home, fake_bin, tmp_path):
    script = tmp_path / 'job.py'
    content = b'#!/usr/bin/python3\n\n# resources\n#SBATCH -p CPU\nprint("ok")\n'
    script.write_bytes(content)
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport sys\n'
                     'sys.stdout.buffer.write(open(sys.argv[1],"rb").read())\n')
    result = invoke([str(script), 'argument'])
    assert result.returncode == 0
    assert result.stdout == content
    assert script.read_bytes() == content


def test_live_output_does_not_wait_for_native_exit(isolated_home, fake_bin, tmp_path):
    import select
    gate = tmp_path / 'release'
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport os,time\n'
                     'os.write(1,b"Submitted batch job 12345\\n")\n'
                     f'while not os.path.exists({str(gate)!r}): time.sleep(.01)\n')
    process = subprocess.Popen([sys.executable, '-m', 'cslurm.cli.csbatch', '--wait', '--wrap=true'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready
        assert process.stdout.readline() == b'Submitted batch job 12345\n'
        assert process.poll() is None
    finally:
        gate.touch()
        process.communicate(timeout=10)


def test_script_args_do_not_override_metadata(isolated_home, fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv('CSLURM_STATIC_ANALYSIS', 'false')
    monkeypatch.setenv('CSLURM_AI_AUTO_SUMMARY', 'false')
    monkeypatch.setenv('SBATCH_PARTITION', 'ENV')
    script = tmp_path / 'job.slurm'
    script.write_text('#!/bin/sh\n\n# comment\n#SBATCH -p SCRIPT\ntrue\n#SBATCH -J ignored\n')
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    from cslurm.cli.csbatch import submit_batch
    submit_batch(['--chdir', str(tmp_path), str(script), '-p', 'ARG', '--help'])
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        row = conn.execute('select partition,job_name,effective_chdir,stdout_path from jobs').fetchone()
    assert row == ('ENV', 'job.slurm', str(tmp_path), str(tmp_path / 'slurm-12345.out'))


def test_concurrent_same_filename_uses_original_scripts(isolated_home, fake_bin, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    write_executable(fake_bin / 'sbatch', f'#!{sys.executable}\nimport sys\n'
                     'print(open(sys.argv[1]).read(),end="")\n')
    scripts = []
    for i in range(8):
        directory = tmp_path / str(i)
        directory.mkdir()
        script = directory / 'job.slurm'
        script.write_text(f'#!/bin/sh\necho {i}\n')
        scripts.append(script)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda p: invoke([str(p)]), scripts))
    assert [r.stdout for r in results] == [p.read_bytes() for p in scripts]


def test_pending_receipt_is_ingested_by_worker(isolated_home, fake_bin, monkeypatch):
    monkeypatch.setenv('CSLURM_STATIC_ANALYSIS', 'true')
    monkeypatch.setenv('CSLURM_AI_AUTO_SUMMARY', 'true')
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    assert invoke(['--wrap=true']).returncode == 0
    from cslurm.work import drain
    executed = []
    assert drain(handler=lambda item: executed.append(item['kind'])) == 2
    assert executed == ['static', 'summary']


def test_cluster_and_quiet_receipts_are_not_guessed_into_local_jobs(isolated_home, fake_bin):
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "12345;remote-cluster\\n"\n')
    assert invoke(['--parsable', '--wrap=true']).stdout == b'12345;remote-cluster\n'
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nexit 0\n')
    assert invoke(['--quiet', '--wrap=true']).returncode == 0
    from cslurm.slurm.submissions import ingest
    assert ingest() == 0
    assert len(list((isolated_home / 'spool/sbatch/unresolved').glob('*.json'))) == 2
    assert not (isolated_home / 'db.sqlite').exists()


def test_wrap_operand_named_help_is_not_an_informational_invocation(isolated_home, fake_bin):
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    result = invoke(['--wrap', '--help'])
    assert result.returncode == 0
    receipts = list((isolated_home / 'spool/sbatch').glob('*.json'))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())['job_id'] == '12345'


def test_snapshot_keeps_original_bytes(isolated_home, fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv('CSLURM_STATIC_ANALYSIS', 'false')
    monkeypatch.setenv('CSLURM_AI_AUTO_SUMMARY', 'false')
    script = tmp_path / 'job.slurm'
    content = b'#!/bin/sh\n# non-utf8: \xff\r\ntrue\n'
    script.write_bytes(content)
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    from cslurm.cli.csbatch import submit_batch
    assert submit_batch([str(script)]) == '12345'
    assert (isolated_home / 'jobs/12345/original.slurm').read_bytes() == content


@pytest.mark.parametrize('invalid', ['[]', '{}', '{"job_id":"../outside"}', '{'])
def test_invalid_receipt_does_not_block_other_submissions(isolated_home, fake_bin, invalid):
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\nprintf "Submitted batch job 12345\\n"\n')
    assert invoke(['--wrap=true']).returncode == 0
    directory = isolated_home / 'spool/sbatch'
    (directory / '000-invalid.json').write_text(invalid)
    from cslurm.slurm.submissions import ingest
    assert ingest() == 1
    assert (directory / 'invalid/000-invalid.json').read_text() == invalid


def test_renamed_entrypoint_is_skipped_without_changing_job_path(isolated_home, fake_bin, tmp_path, monkeypatch):
    native_bin = tmp_path / 'native-bin'
    native_bin.mkdir()
    # Recognizable pip entrypoint prefix, with a harmless stand-in instead of recursion.
    write_executable(fake_bin / 'sbatch', '#!/bin/sh\n# from cslurm.cli.csbatch import main\nprintf "wrong wrapper\\n"\n')
    write_executable(native_bin / 'sbatch', '#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    path = str(fake_bin) + os.pathsep + str(native_bin) + os.pathsep + os.environ['PATH']
    monkeypatch.setenv('PATH', path)
    monkeypatch.delenv('CSLURM_SBATCH', raising=False)
    result = invoke(['--version'])
    assert result.returncode == 0
    assert result.stdout == (path + '\n').encode()
