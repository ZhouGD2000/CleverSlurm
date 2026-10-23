"""Private SSH profiles and per-workspace transports; never switch global roots."""

import fcntl
import base64
import hashlib
import json
import os
import re
import select
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

from cslurm.web.operations import BusyError


class RemoteError(Exception):
    pass


def validate(profile):
    fields = {'id', 'name', 'alias', 'python', 'root', 'install_dir', 'read_only'}
    if not isinstance(profile, dict) or set(profile) - fields:
        raise ValueError('Unsupported server fields')
    item = {key: profile.get(key, default) for key, default in {
        'name': '', 'alias': '', 'python': 'python3', 'root': '~/.cslurm',
        'install_dir': '~/.local/share/cleverslurm', 'read_only': True,
    }.items()}
    for key in ('name', 'alias', 'python', 'root', 'install_dir'):
        if not isinstance(item[key], str) or not 0 < len(item[key]) <= (80 if key == 'name' else 1024):
            raise ValueError('Invalid server field')
        if any(ord(c) < 32 for c in item[key]):
            raise ValueError('Control characters are not allowed')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item['alias']):
        raise ValueError('Use an SSH alias, not SSH arguments')
    if not (re.fullmatch(r'[A-Za-z0-9_.-]+', item['python']) or item['python'].startswith(('/', '~/'))):
        raise ValueError('Python must be an executable name or absolute path')
    if any(not item[key].startswith(('/', '~/')) for key in ('root', 'install_dir')):
        raise ValueError('Use absolute or home-relative paths')
    if not isinstance(item['read_only'], bool):
        raise ValueError('read_only must be boolean')
    identifier = profile.get('id') or uuid.uuid4().hex
    if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('Invalid server ID')
    return {'id': identifier, **item}


class Registry:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'servers.json'
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            content = self.path.read_bytes() if self.path.exists() else b'[]'
            items = json.loads(content)
            if not isinstance(items, list):
                raise ValueError('Invalid server registry')
            return {'revision': hashlib.sha256(content).hexdigest(), 'items': [validate(item) for item in items]}

    @staticmethod
    def _write(path, content):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _change(self, revision, change):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / 'servers.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                current = self.snapshot()
                if revision != current['revision']:
                    raise ValueError('Server list changed; reload before saving')
                items = change(current['items'])
                if self.path.exists():
                    backups = self.root / 'server-backups'
                    backups.mkdir(mode=0o700, exist_ok=True)
                    self._write(backups / (uuid.uuid4().hex + '.json'), self.path.read_bytes())
                temporary = self.root / ('servers-' + uuid.uuid4().hex + '.tmp')
                self._write(temporary, json.dumps(items, ensure_ascii=True, indent=2).encode())
                temporary.replace(self.path)
                return self.snapshot()

    def save(self, profile, revision):
        item = validate(profile)
        def change(items):
            if profile.get('id') and not any(old['id'] == item['id'] for old in items):
                raise KeyError('Server not found')
            remaining = [old for old in items if old['id'] != item['id']]
            if any(old['alias'] == item['alias'] and old['root'] == item['root'] for old in remaining):
                raise ValueError('SSH alias and data root already registered')
            if len(remaining) >= 32:
                raise ValueError('At most 32 servers are supported')
            return [*remaining, item]
        return self._change(revision, change)

    def remove(self, identifier, revision):
        def change(items):
            if not any(item['id'] == identifier for item in items):
                raise KeyError('Server not found')
            return [item for item in items if item['id'] != identifier]
        return self._change(revision, change)

    def get(self, identifier):
        for item in self.snapshot()['items']:
            if item['id'] == identifier:
                return item
        raise KeyError('Server not found')


def ssh_command(profile, source, options=None):
    python = profile['python']
    executable = '"$HOME"/' + shlex.quote(python[2:]) if python.startswith('~/') else shlex.quote(python)
    command = 'exec ' + executable + ' -u -c ' + shlex.quote(source)
    if options is not None:
        command += ' ' + shlex.quote(json.dumps(options, ensure_ascii=True))
    return ssh_transport(profile['alias'], command)


def ssh_transport(alias, command):
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=2', '--', alias, command]


def allowed_path(path):
    return (isinstance(path, str) and len(path) <= 8192 and not any(ord(c) < 32 for c in path)
            and re.fullmatch(r'/api/(?:overview|jobs(?:/[A-Za-z0-9_.-]+(?:/logs)?)?|health|compare|'
                             r'settings(?:/test|/models)?|operations(?:/[0-9a-f]{32})?)(?:\?[^#]*)?', path))


class Bridge:
    def __init__(self, profile, *, read_only=False, allow_cancel=False):
        from cslurm.web import remote_bridge
        self.lock = threading.RLock()
        self.buffer, self.closed = b'', False
        self.read_only = read_only or profile['read_only']
        options = {'root': profile['root'], 'read_only': self.read_only,
                   'allow_cancel': allow_cancel and not self.read_only}
        self.process = subprocess.Popen(ssh_command(profile, Path(remote_bridge.__file__).read_text(), options),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self.info = self._read(18)
            if not isinstance(self.info, dict) or self.info.get('status') not in {'connected', 'not_installed', 'incompatible'}:
                raise RemoteError('Unrecognized remote handshake')
            if self.info['status'] != 'connected':
                self.close()
        except Exception:
            self.close()
            raise

    def _read(self, timeout):
        deadline = time.monotonic() + timeout
        while b'\n' not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                raise RemoteError('SSH request timed out; outcome may be unknown. No request was retried')
            part = os.read(self.process.stdout.fileno(), 65536)
            if not part:
                raise RemoteError('SSH disconnected. Check alias, known_hosts, key/agent and remote Python')
            self.buffer += part
            if len(self.buffer) > 12 * 1024 * 1024:
                raise RemoteError('SSH response exceeds size limit')
        line, self.buffer = self.buffer.split(b'\n', 1)
        try:
            return json.loads(line)
        except ValueError:
            raise RemoteError('Invalid SSH response; check shell startup output') from None

    def request(self, path, body=None):
        if not allowed_path(path):
            raise ValueError('Remote endpoint is not allowed')
        if body is not None and self.read_only:
            return 403, {'error': 'Read-only server: operations are disabled'}
        message = json.dumps({'path': path, 'body': body}, ensure_ascii=True).encode() + b'\n'
        if len(message) > 65536:
            raise ValueError('Request exceeds SSH message limit')
        with self.lock:
            if self.closed:
                raise RemoteError('SSH is disconnected; reconnect explicitly')
            try:
                self.process.stdin.write(message)
                self.process.stdin.flush()
                result = self._read(70)
                if not isinstance(result, dict) or not isinstance(result.get('status'), int) or 'data' not in result:
                    raise RemoteError('Invalid remote API response')
                return result['status'], result['data']
            except (RemoteError, OSError):
                self.close()
                raise RemoteError('SSH request failed; outcome may be unknown. Reconnect and inspect before retrying') from None

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdout.close()


class RemoteManager:
    def __init__(self, root, *, read_only=False, allow_cancel=False, demo=False):
        self.registry = Registry(root)
        self.read_only, self.allow_cancel, self.demo = read_only, allow_cancel, demo
        self.lock = threading.RLock()
        self.bridges, self.states = {}, {}
        self.installing = set()
        self.discovery_slots = threading.BoundedSemaphore(2)

    def snapshot(self):
        snapshot = self.registry.snapshot()
        with self.lock:
            return {**snapshot, 'management_read_only': self.read_only or self.demo,
                    'items': [{**item, 'connection': self.states.get(item['id'], {'status': 'disconnected'})}
                              for item in snapshot['items']]}

    def _disconnect(self, identifier):
        bridge = self.bridges.get(identifier)
        if bridge:
            # The active-operation check and close must be indivisible with
            # respect to another browser tab starting a remote operation.
            with bridge.lock:
                if not bridge.closed:
                    status, overview = bridge.request('/api/overview')
                    if status != 200 or overview.get('active_operation'):
                        raise BusyError('Remote operation active or unknown; inspect before disconnecting')
                bridge.close()
            self.bridges.pop(identifier, None)
        self.states[identifier] = {'status': 'disconnected'}

    def action(self, body):
        action, identifier = body.get('action'), body.get('id')
        if self.demo or (self.read_only and action not in {'connect', 'disconnect'}):
            raise PermissionError('Read-only/demo console: server changes disabled')
        with self.lock:
            item = body.get('profile')
            if action == 'save':
                validate(item)
            changed = identifier or (item.get('id') if isinstance(item, dict) else None)
            if changed in self.installing:
                raise BusyError('Installation is running for this server')
            if action in {'save', 'remove'} and body.get('revision') != self.registry.snapshot()['revision']:
                raise ValueError('Server list changed; reload before saving')
            if action == 'save':
                if isinstance(item, dict) and item.get('id'):
                    self._disconnect(item['id'])
                self.registry.save(item, body.get('revision'))
            elif action == 'remove':
                self.registry.get(identifier)
                self._disconnect(identifier)
                self.registry.remove(identifier, body.get('revision'))
            elif action == 'install':
                profile = self.registry.get(identifier)
                if body.get('confirm_alias') != profile['alias']:
                    raise ValueError('Type the exact SSH alias to confirm installation')
                if self.states.get(identifier, {}).get('status') != 'not_installed':
                    raise BusyError('Connect first and confirm the selected Python has no CleverSlurm installation')
                self.installing.add(identifier)
                self.states[identifier] = {'status': 'installing', 'phase': 'building'}
                threading.Thread(target=self._install, args=(profile,), daemon=True).start()
            elif action in {'connect', 'disconnect'}:
                profile = self.registry.get(identifier)
                self._disconnect(identifier)
                if action == 'connect':
                    try:
                        bridge = Bridge(profile, read_only=self.read_only, allow_cancel=self.allow_cancel)
                        self.states[identifier] = bridge.info
                        if not bridge.closed:
                            self.bridges[identifier] = bridge
                    except (OSError, RemoteError) as exc:
                        self.states[identifier] = {'status': 'error', 'error': str(exc)}
            else:
                raise ValueError('Unknown server action')
        return self.snapshot()

    def _install(self, profile):
        from cslurm.web.remote_package import build_wheel
        from cslurm.web import remote_install
        identifier = profile['id']
        try:
            artifact = self.registry.root / 'install-artifacts' / uuid.uuid4().hex
            wheel = build_wheel(artifact)
            payload = {'destination': profile['install_dir'], 'root': profile['root'], 'filename': wheel.name,
                       'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
                       'wheel': base64.b64encode(wheel.read_bytes()).decode()}
            with self.lock:
                self.states[identifier] = {'status': 'installing', 'phase': 'uploading_installing'}
            result = subprocess.run(ssh_command(profile, Path(remote_install.__file__).read_text()),
                                    input=json.dumps(payload).encode() + b'\n', capture_output=True, timeout=300)
            try:
                response = json.loads(result.stdout)
            except ValueError:
                raise RemoteError('Installer connection failed; inspect the destination before retrying') from None
            if result.returncode or response.get('status') != 'installed':
                raise RemoteError(response.get('error', 'Remote installation failed; files retained'))
            with self.lock:
                revision = self.registry.snapshot()['revision']
                # Another cweb process may have edited the saved profile.
                if self.registry.get(identifier) == profile:
                    self.registry.save({**profile, 'python': response['python']}, revision)
                self.states[identifier] = response
        except subprocess.TimeoutExpired:
            with self.lock:
                self.states[identifier] = {'status': 'install_failed', 'error': 'Installation timed out; inspect retained files before retrying',
                                           'destination': profile['install_dir']}
        except Exception as exc:
            with self.lock:
                self.states[identifier] = {'status': 'install_failed', 'error': str(exc),
                                           'destination': profile['install_dir']}
        finally:
            with self.lock:
                self.installing.discard(identifier)

    def request(self, identifier, path, body=None):
        self.registry.get(identifier)
        with self.lock:
            bridge = self.bridges.get(identifier)
        if not bridge or bridge.closed:
            raise BusyError('Server not connected; connect explicitly')
        try:
            return bridge.request(path, body)
        except RemoteError:
            with self.lock:
                self.states[identifier] = {'status': 'error', 'error': 'SSH disconnected; request outcome may be unknown'}
            raise

    def close(self):
        for bridge in list(self.bridges.values()):
            bridge.close()
