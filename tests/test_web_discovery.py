import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import write_executable
from test_web import request, web_db, server


def test_ssh_aliases_include_deduplicate_and_never_execute(tmp_path):
    from cslurm.web.discovery import ssh_aliases
    ssh = tmp_path / '.ssh'
    ssh.mkdir()
    (ssh / 'config').write_text('Host cluster other *.example !excluded\n'
                                ' HostName secret.example\n IdentityFile secret-key\n'
                                'Include "extra conf"\nMatch exec "touch never"\n'
                                ' HostName ignored\nHost cluster\nInclude config\n')
    (ssh / 'extra conf').write_text('Host=mgmt\nHost "second"\n')
    result = ssh_aliases(ssh / 'config')
    assert [i['alias'] for i in result['items']] == ['cluster', 'mgmt', 'other', 'second']
    assert 'secret' not in json.dumps(result)
    assert not (tmp_path / 'never').exists()
    assert result['warnings']


def test_missing_config_and_bounded_files(tmp_path):
    from cslurm.web.discovery import ssh_aliases
    config = tmp_path / 'config'
    assert ssh_aliases(config)['items'] == []
    config.write_text('Host huge\n' + 'x' * 300000)
    result = ssh_aliases(config)
    assert not result['items'] and result['warnings']


def test_probe_script_reads_environment_without_initializing_data(tmp_path):
    from cslurm.web import remote_discovery
    root = tmp_path / 'absent-data'
    result = subprocess.run([sys.executable, '-B', str(Path(remote_discovery.__file__))],
                            env={**os.environ, 'CSLURM_ROOT': str(root)},
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    installed = [i for i in data['environments'] if i['installed']]
    assert installed and installed[0]['package'].endswith('cslurm/__init__.py')
    assert installed[0]['root'] == str(root)
    assert installed[0]['root_source'] == 'environment'
    assert not root.exists()


def test_discovery_over_fake_ssh_and_invalid_alias(fake_bin, tmp_path):
    from cslurm.web.discovery import discover
    from cslurm.web.remotes import RemoteError
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\n'
                     'import os,shlex,sys\n'
                     'a=shlex.split(sys.argv[-1]); assert a.pop(0)=="exec"\n'
                     'os.execv(a[0],a)\n')
    result = discover('cluster')
    assert result['environments'] and result['alias'] == 'cluster'
    with pytest.raises(ValueError):
        discover('-oProxyCommand=evil')
    write_executable(fake_bin / 'ssh', '#!/bin/sh\nexit 255\n')
    with pytest.raises(RemoteError, match='SSH'):
        discover('cluster')


def test_discovery_routes_are_authenticated_local_and_demo_blocked(server, monkeypatch):
    from cslurm.web import discovery
    monkeypatch.setattr(discovery, 'ssh_aliases', lambda: {'items': [{'alias': 'cluster'}], 'warnings': []})
    monkeypatch.setattr(discovery, 'discover', lambda alias: {'alias': alias, 'environments': []})
    assert request(server, '/api/servers/ssh', auth=False)[0] == 403
    assert request(server, '/api/servers/discover', auth=False, body={'alias': 'cluster'})[0] == 403
    assert request(server, '/api/servers/ssh', headers={'X-CSlurm-Server': 'unknown'})[1]['items']
    assert request(server, '/api/servers/discover', body={'alias': 'cluster'})[1]['alias'] == 'cluster'
    assert request(server, '/api/overview')[1]['total'] == 3
    server.operations.demo = True
    assert request(server, '/api/servers/discover', body={'alias': 'cluster'})[0] == 403
    assert request(server, '/api/servers/ssh')[0] == 403
    server.operations.demo = False
    server.operations.read_only = True
    assert request(server, '/api/servers/discover', body={'alias': 'cluster'})[0] == 200


def test_discovery_ui_controls_exist(server):
    html = request(server, '/')[1].decode()
    for name in ('server-ssh-options', 'server-detect', 'server-environment', 'server-advanced'):
        assert f'id="{name}"' in html


def test_same_prefix_different_python_versions_are_not_hidden(monkeypatch):
    from cslurm.web import remote_discovery as probe
    monkeypatch.setattr(probe, 'candidates', lambda: ['old', 'new'])
    def run(args, **kwargs):
        supported = args[0] == 'new'
        item = dict(python=args[0], prefix='/usr', package=None, installed=False,
                    python_version='3.11.0' if supported else '3.9.0', supported=supported, web=False)
        return subprocess.CompletedProcess(args, 0, json.dumps(item))
    monkeypatch.setattr(probe, 'run_probe', lambda python, timeout: run([python]))
    result = probe.inspect()
    assert len(result['environments']) == 2
    assert result['environments'][0]['supported']


def test_failed_python_does_not_hide_other_environments(monkeypatch):
    from cslurm.web import remote_discovery as probe
    monkeypatch.setattr(probe, 'candidates', lambda: ['/missing', sys.executable])
    result = probe.inspect()
    assert result['warnings'] and result['environments']


def test_detect_does_not_import_package(tmp_path):
    from cslurm.web.remote_discovery import PROBE
    package = tmp_path / 'cslurm'
    package.mkdir()
    (package / '__init__.py').write_text('raise RuntimeError("must not import")')
    result = subprocess.run([sys.executable, '-B', '-c', PROBE],
                            env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['installed']
    assert not (package / '__pycache__').exists()


@pytest.mark.parametrize('output', ['not json', '{"status":"ok"}', 'x' * 270000])
def test_bad_or_oversized_detection_output_is_not_missing_install(fake_bin, output):
    from cslurm.web.discovery import discover
    from cslurm.web.remotes import RemoteError
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\nprint({output!r})\n')
    with pytest.raises(RemoteError):
        discover('cluster')


def test_no_bootstrap_python_is_reported_explicitly(fake_bin):
    from cslurm.web.discovery import discover
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\n'
                     'print(\'{"status":"no_python","environments":[],"warnings":[]}\')\n')
    assert discover('cluster')['status'] == 'no_python'


def test_config_symlink_loop_does_not_crash(tmp_path):
    from cslurm.web.discovery import ssh_aliases
    config = tmp_path / 'config'
    config.symlink_to('config')
    result = ssh_aliases(config)
    assert not result['items'] and result['warnings']


def test_probe_output_and_time_are_bounded(tmp_path):
    from cslurm.web.remote_discovery import run_probe
    noisy = tmp_path / 'python-noisy'
    write_executable(noisy, f'#!{sys.executable}\nprint("x" * 100000)\n')
    with pytest.raises(ValueError):
        run_probe(str(noisy), 1)
    slow = tmp_path / 'python-slow'
    write_executable(slow, f'#!{sys.executable}\nimport time\ntime.sleep(5)\n')
    with pytest.raises(subprocess.TimeoutExpired):
        run_probe(str(slow), 0.1)


def test_detection_concurrency_is_limited(server):
    slots = server.remotes.discovery_slots
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    try:
        assert request(server, '/api/servers/discover', body={'alias': 'cluster'})[0] == 409
    finally:
        slots.release()
        slots.release()


@pytest.mark.parametrize('close_stdout', [False, True])
def test_ssh_timeout_is_an_explicit_detection_error(fake_bin, monkeypatch, close_stdout):
    from cslurm.web import discovery
    from cslurm.web.remotes import RemoteError
    monkeypatch.setattr(discovery, 'SSH_DISCOVERY_TIMEOUT', 0.1)
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\nimport os,time\n'
                     + ('os.close(1)\n' if close_stdout else '') + 'time.sleep(5)\n')
    with pytest.raises(RemoteError, match='timed out'):
        discovery.discover('cluster')
