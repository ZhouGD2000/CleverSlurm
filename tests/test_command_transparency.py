import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from conftest import write_executable


def invoke(name, args, **kwargs):
    return subprocess.run([sys.executable, '-m', 'cslurm.cli.c' + name, *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, **kwargs)


@pytest.mark.parametrize('name', ['srun', 'scancel'])
@pytest.mark.parametrize('args', [[], ['--help'], ['--version'], ['--unknown=value', '--', 'a b', ''],
                                ['--note', 'unchanged'], ['-s', '9', '12345']])
def test_arguments_are_never_filtered(isolated_home, fake_bin, tmp_path, name, args):
    calls = tmp_path / 'calls.json'
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport json,sys\n'
                     f'open({str(calls)!r},"w").write(json.dumps(sys.argv[1:]))\n')
    result = invoke(name, args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == b''
    assert json.loads(calls.read_text()) == args


@pytest.mark.parametrize('name', ['srun', 'scancel'])
@pytest.mark.parametrize('code', [0, 7, 42])
def test_bytes_and_exit_status_ignore_broken_storage(fake_bin, tmp_path, monkeypatch, name, code):
    root = tmp_path / 'not-a-directory'
    root.write_text('preserved')
    monkeypatch.setenv('CSLURM_ROOT', str(root))
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport os\n'
                     f'os.write(1,b"stdout\\xff\\n");os.write(2,b"stderr\\xfe\\n")\nraise SystemExit({code})\n')
    result = invoke(name, ['12345'])
    assert (result.returncode, result.stdout, result.stderr) == (code, b'stdout\xff\n', b'stderr\xfe\n')
    assert root.read_text() == 'preserved'


@pytest.mark.parametrize('name', ['srun', 'scancel'])
def test_stdin_and_inherited_fd(isolated_home, fake_bin, tmp_path, name):
    data = tmp_path / 'fd-data'
    data.write_bytes(b'export-data\0')
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport os,sys\n'
                     'os.write(1,sys.stdin.buffer.read());os.write(2,os.read(int(sys.argv[1]),100))\n')
    with data.open('rb') as stream:
        result = invoke(name, [str(stream.fileno())], input=b'yes\n', pass_fds=(stream.fileno(),))
    assert result.returncode == 0
    assert result.stdout == b'yes\n'
    assert result.stderr == b'export-data\0'


def test_interactive_cancel_prompt_is_visible_before_input(isolated_home, fake_bin):
    write_executable(fake_bin / 'scancel', f'#!{sys.executable}\nimport os,sys\n'
                     'os.write(1,b"Cancel? "); answer=sys.stdin.readline(); '
                     'os.write(1,("answer="+answer).encode())\n')
    process = subprocess.Popen([sys.executable, '-m', 'cslurm.cli.cscancel', '-i', '12345'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert os.read(process.stdout.fileno(), 8) == b'Cancel? '
        output, error = process.communicate(b'n\n', timeout=5)
        assert (process.returncode, output, error) == (0, b'answer=n\n', b'')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('name', ['srun', 'scancel'])
def test_direct_signal_is_forwarded_without_terminal(isolated_home, fake_bin, name):
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport os,signal\n'
                     'signal.signal(signal.SIGUSR1, lambda *args: os._exit(19))\n'
                     'os.write(1,b"ready\\n")\nwhile True: signal.pause()\n')
    process = subprocess.Popen([sys.executable, '-m', 'cslurm.cli.c' + name, '12345'],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline() == b'ready\n'
        process.send_signal(signal.SIGUSR1)
        _, error = process.communicate(timeout=5)
        assert (process.returncode, error) == (19, b'')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize('name', ['srun', 'scancel'])
def test_cli_does_not_wait_for_database_lock(isolated_home, fake_bin, name):
    from cslurm.db import connect, init_db
    write_executable(fake_bin / name, '#!/bin/sh\nexit 0\n')
    with connect() as conn:
        init_db(conn)
        conn.execute('begin exclusive')
        result = invoke(name, ['12345'])
    assert result.returncode == 0
    kind = 'csrun' if name == 'srun' else 'scancel'
    assert len(list((isolated_home / 'spool' / kind).glob('*.json'))) == 1


@pytest.mark.parametrize('name', ['srun', 'scancel'])
@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGKILL])
def test_native_signal_termination(isolated_home, fake_bin, name, signum):
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport os\nos.kill(os.getpid(),{int(signum)})\n')
    result = invoke(name, ['12345'])
    assert result.returncode == -signum
    assert result.stderr == b''


@pytest.mark.parametrize('name', ['srun', 'scancel'])
def test_tracking_off_keeps_native_pid(isolated_home, fake_bin, monkeypatch, name):
    monkeypatch.setenv('CSLURM_TRACK_COMMANDS', 'false')
    write_executable(fake_bin / name, f'#!{sys.executable}\nimport os\nprint(os.getpid())\n')
    process = subprocess.Popen([sys.executable, '-m', 'cslurm.cli.c' + name, '--version'], stdout=subprocess.PIPE)
    output, _ = process.communicate(timeout=8)
    assert output == f'{process.pid}\n'.encode()
    assert not (isolated_home / 'spool').exists()


@pytest.mark.parametrize('name', ['srun', 'scancel'])
def test_renamed_entrypoint_is_not_recursed(isolated_home, fake_bin, tmp_path, monkeypatch, name):
    native = tmp_path / 'native'
    native.mkdir()
    write_executable(fake_bin / name, f'#!/bin/sh\n# from cslurm.cli.c{name} import main\nprintf "wrong\\n"\n')
    write_executable(native / name, '#!/bin/sh\nprintf "%s\\n" "$PATH"\n')
    path = str(fake_bin) + os.pathsep + str(native) + os.pathsep + os.environ['PATH']
    monkeypatch.setenv('PATH', path)
    monkeypatch.delenv('CSLURM_' + name.upper(), raising=False)
    assert invoke(name, ['--version']).stdout == (path + '\n').encode()


def _read_until(fd, expected, timeout=5):
    end, data = time.monotonic() + timeout, b''
    while expected not in data and time.monotonic() < end:
        if select.select([fd], [], [], max(0, end - time.monotonic()))[0]:
            data += os.read(fd, 4096)
    assert expected in data, data
    return data


def test_srun_terminal_ctrl_c_is_delivered_once(isolated_home, fake_bin):
    import pty
    write_executable(fake_bin / 'srun', f'#!{sys.executable}\nimport os,signal,time\n'
                     'def on_int(s,f): os.write(1,b"INT\\n")\n'
                     'signal.signal(signal.SIGINT,on_int)\n'
                     'print("TTY="+str(all(os.isatty(i) for i in (0,1,2))),flush=True)\n'
                     'input()\nprint("DONE",flush=True)\n')
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, '-m', 'cslurm.cli.csrun', '--pty', 'sh'])
    try:
        _read_until(fd, b'TTY=True')
        os.write(fd, b'\x03')
        output = _read_until(fd, b'INT')
        os.write(fd, b'\n')
        output += _read_until(fd, b'DONE')
        deadline = time.monotonic() + 5
        while True:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                break
            assert time.monotonic() < deadline, output
            if select.select([fd], [], [], .05)[0]:
                try:
                    output += os.read(fd, 4096)
                except OSError:
                    pass
        assert os.waitstatus_to_exitcode(status) == 0
        assert output.count(b'INT') == 1
        assert b'KeyboardInterrupt' not in output
    finally:
        os.close(fd)
