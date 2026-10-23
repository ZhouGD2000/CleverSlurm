import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import write_executable
from test_web import request, web_db, server


def profile(**changes):
    return dict(name='Cluster', alias='cluster', python=sys.executable,
                root='~/.cslurm', install_dir='~/.local/share/cleverslurm',
                read_only=True, **changes)


def test_registry_is_private_revision_checked_and_preserves_config(isolated_home):
    from cslurm.web.remotes import Registry
    config = isolated_home / 'config.toml'
    config.write_text('[ai]\napi_key="unchanged"\n')
    registry = Registry(isolated_home)
    before = registry.snapshot()
    saved = registry.save(profile(), before['revision'])
    assert len(saved['items']) == 1
    assert (isolated_home / 'servers.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        registry.save(profile(), before['revision'])
    item = saved['items'][0]
    saved = registry.save({**item, 'name': 'Updated'}, saved['revision'])
    assert saved['items'][0]['name'] == 'Updated'
    registry.remove(item['id'], saved['revision'])
    assert not registry.snapshot()['items']
    assert config.read_text() == '[ai]\napi_key="unchanged"\n'
    assert list((isolated_home / 'server-backups').glob('*.json'))


@pytest.mark.parametrize('field,value', [
    ('alias', '-oProxyCommand=bad'), ('alias', 'host; touch bad'),
    ('python', 'python3 -c evil'), ('root', 'relative/path'),
    ('name', ''), ('read_only', 'false'), ('password', 'do not store'),
])
def test_invalid_server_fields_are_rejected(isolated_home, field, value):
    from cslurm.web.remotes import Registry
    registry = Registry(isolated_home)
    with pytest.raises(ValueError):
        registry.save({**profile(), field: value}, registry.snapshot()['revision'])
    assert not (isolated_home / 'servers.json').exists()


def test_ssh_command_is_strict_and_quoted():
    from cslurm.web.remotes import ssh_command
    args = ssh_command(profile(), 'print("hello")')
    assert args[:2] == ['ssh', '-T']
    for flag in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ForwardAgent=no', 'ClearAllForwardings=yes'):
        assert flag in args
    assert args[-2] == 'cluster'
    assert shlex.split(args[-1]) == ['exec', sys.executable, '-u', '-c', 'print("hello")']


@pytest.fixture
def fake_ssh(fake_bin, tmp_path):
    calls = tmp_path / 'ssh-calls.jsonl'
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\n'
                     'import json,os,shlex,sys\n'
                     f'with open({str(calls)!r},"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                     'args=shlex.split(sys.argv[-1]); assert args.pop(0)=="exec"\n'
                     'os.execv(args[0],args)\n')
    return calls


def test_bridge_reads_existing_database_without_writes(web_db, fake_ssh):
    from cslurm.web.remotes import Bridge
    before = (web_db / 'db.sqlite').read_bytes()
    bridge = Bridge({**profile(), 'root': str(web_db)})
    try:
        assert bridge.info['status'] == 'connected'
        status, data = bridge.request('/api/jobs')
        assert status == 200 and data['total'] == 3
        assert bridge.request('/api/operations', {'action': 'refresh'})[0] == 403
        with pytest.raises(ValueError):
            bridge.request('http://example.com/api/jobs')
        with pytest.raises(ValueError):
            bridge.request('/api/servers')
    finally:
        bridge.close()
    assert (web_db / 'db.sqlite').read_bytes() == before
    assert not (web_db / 'config.toml').exists()
    assert len(fake_ssh.read_text().splitlines()) == 1


def test_local_workspace_and_management_auth_are_preserved(server):
    assert request(server, '/api/overview')[1]['total'] == 3
    assert request(server, '/api/servers', auth=False)[0] == 403
    code, snapshot = request(server, '/api/servers')
    assert code == 200 and snapshot['items'] == []
    code, saved = request(server, '/api/servers', body={
        'action': 'save', 'profile': profile(), 'revision': snapshot['revision']})
    assert code == 200 and len(saved['items']) == 1
    assert request(server, '/api/overview')[1]['total'] == 3
    assert request(server, '/api/jobs', headers={'X-CSlurm-Server': 'unknown'})[0] == 404
    server.operations.read_only = True
    assert request(server, '/api/servers', body={'action': 'save', 'profile': profile(),
                   'revision': saved['revision']})[0] == 403


def test_server_target_routing_and_no_automatic_reconnect(server, fake_ssh, web_db):
    _, snapshot = request(server, '/api/servers')
    _, saved = request(server, '/api/servers', body={'action': 'save',
        'profile': {**profile(), 'root': str(web_db)}, 'revision': snapshot['revision']})
    identifier = saved['items'][0]['id']
    header = {'X-CSlurm-Server': identifier}
    assert request(server, '/api/jobs', headers=header)[0] == 409
    assert request(server, '/api/servers', body={'action': 'connect', 'id': identifier})[0] == 200
    assert request(server, '/api/overview', headers=header)[1]['read_only'] is True
    assert request(server, '/api/jobs', headers=header)[1]['total'] == 3
    assert request(server, '/api/operations', headers=header, body={'action': 'cancel', 'job_id': '100'})[0] == 403
    assert request(server, '/api/servers', body={'action': 'disconnect', 'id': identifier})[0] == 200
    assert request(server, '/api/jobs', headers=header)[0] == 409
    assert request(server, '/api/overview')[1]['total'] == 3
    assert len(fake_ssh.read_text().splitlines()) == 1


def test_remote_install_requires_explicit_confirmation_and_missing_package(server):
    _, initial = request(server, '/api/servers')
    _, saved = request(server, '/api/servers', body={'action': 'save', 'profile': profile(), 'revision': initial['revision']})
    identifier = saved['items'][0]['id']
    assert request(server, '/api/servers', body={'action': 'install', 'id': identifier})[0] == 400
    assert request(server, '/api/servers', body={'action': 'install', 'id': identifier, 'confirm_alias': 'cluster'})[0] == 409


def test_installer_refuses_existing_directory_and_symlink(isolated_home, tmp_path):
    from cslurm.web.remote_install import destination
    home = Path(os.environ['HOME'])
    folder = home / 'preserved'
    folder.mkdir()
    (folder / 'keep.txt').write_text('untouched')
    with pytest.raises(FileExistsError):
        destination(str(folder))
    link = home / 'linked'
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError):
        destination(str(link / 'new'))
    with pytest.raises(ValueError):
        destination('/tmp/outside-home')
    assert (folder / 'keep.txt').read_text() == 'untouched'


def test_local_wheel_installs_into_fresh_venv_without_touching_config(isolated_home):
    from cslurm.web.remote_install import install
    from cslurm.web.remote_package import build_wheel
    import base64
    import hashlib
    wheel = build_wheel(isolated_home / 'artifacts')
    root = isolated_home / 'existing-data'
    root.mkdir()
    (root / 'config.toml').write_text('[ai]\nmodel="keep"\n')
    target = Path(os.environ['HOME']) / 'new-install'
    result = install({'destination': str(target), 'root': str(root), 'filename': wheel.name,
                      'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
                      'wheel': base64.b64encode(wheel.read_bytes()).decode()})
    assert result['status'] == 'installed'
    assert Path(result['python']).exists()
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    check = subprocess.run([result['python'], '-c', 'import cslurm; print(cslurm.__file__)'],
                           env=env, capture_output=True, text=True)
    assert check.returncode == 0 and str(target) in check.stdout
    assert (root / 'config.toml').read_text() == '[ai]\nmodel="keep"\n'
    assert not (root / 'db.sqlite').exists()
    assert not (root / 'notifications').exists()


def test_installer_checks_hash_before_creating_destination(isolated_home):
    from cslurm.web.remote_install import install
    target = Path(os.environ['HOME']) / 'invalid-install'
    with pytest.raises(ValueError):
        install({'destination': str(target), 'root': '~/.cslurm', 'filename': 'fake.whl',
                 'sha256': 'bad', 'wheel': 'bm90IGEgd2hlZWw='})
    assert not target.exists()


def test_workspace_ui_contains_local_choice_and_explicit_installer(server):
    page = request(server, '/', auth=False)[1].decode()
    assert 'id="workspace-select"' in page
    assert '本机（cweb 所在主机）' in page
    assert 'id="servers-dialog"' in page
    assert 'id="server-install-confirm"' in page
    script = request(server, '/servers.js', auth=False)[1].decode()
    assert 'switchWorkspace' in script and 'confirm_alias' in script


def test_missing_package_is_distinct_from_ssh_failure(isolated_home, fake_bin):
    from cslurm.web.remotes import Bridge, RemoteError
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\n'
                     'import os,shlex,sys\na=shlex.split(sys.argv[-1]); a.pop(0)\n'
                     'os.environ.pop("PYTHONPATH",None); a.insert(1,"-S"); os.execv(a[0],a)\n')
    bridge = Bridge(profile())
    assert bridge.info['status'] == 'not_installed'
    assert bridge.closed
    write_executable(fake_bin / 'ssh', '#!/bin/sh\nexit 255\n')
    with pytest.raises(RemoteError):
        Bridge(profile())


def test_disconnect_is_refused_for_active_operation(server, fake_ssh, web_db):
    from cslurm.web.operations import BusyError
    manager = server.remotes
    manager.action({'action': 'save', 'profile': {**profile(), 'root': str(web_db)},
                    'revision': manager.snapshot()['revision']})
    identifier = manager.snapshot()['items'][0]['id']
    manager.action({'action': 'connect', 'id': identifier})
    bridge = manager.bridges[identifier]
    original = bridge.request
    bridge.request = lambda *args: (200, {'active_operation': 'pending'})
    try:
        with pytest.raises(BusyError):
            manager.action({'action': 'disconnect', 'id': identifier})
        assert not bridge.closed
    finally:
        bridge.request = original


def test_malformed_management_request_returns_client_error(server):
    assert request(server, '/api/servers', body={'action': 'save', 'profile': ['bad']})[0] == 400


def test_remote_backup_targets_remote_root_even_with_broken_local_db(server, fake_ssh, web_db, tmp_path):
    import time
    remote = tmp_path / 'remote-backup-root'
    subprocess.run([sys.executable, '-c', 'from cslurm.db import connect,init_db\nwith connect() as c: init_db(c)'],
                   env={**os.environ, 'CSLURM_ROOT': str(remote)}, check=True)
    manager = server.remotes
    manager.action({'action': 'save', 'profile': {**profile(), 'read_only': False, 'root': str(remote)},
                    'revision': manager.snapshot()['revision']})
    identifier = manager.snapshot()['items'][0]['id']
    manager.action({'action': 'connect', 'id': identifier})
    server.database_error = 'Local database unavailable'
    headers = {'X-CSlurm-Server': identifier}
    assert request(server, '/api/jobs', headers=headers)[1]['total'] == 0
    assert request(server, '/api/jobs')[0] == 503
    code, operation = request(server, '/api/operations', body={'action': 'backup'}, headers=headers)
    assert code == 202
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _, result = request(server, '/api/operations/' + operation['id'], headers=headers)
        if result['status'] != 'running': break
        time.sleep(.05)
    assert result['status'] == 'succeeded', result
    assert list((remote / 'backups').glob('*'))
    assert not (web_db / 'backups').exists()
