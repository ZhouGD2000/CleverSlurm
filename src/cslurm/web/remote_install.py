"""Self-contained offline installer. Existing destinations are never reused."""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import venv
from pathlib import Path


def destination(value):
    path = Path(value).expanduser()
    home = Path.home().resolve()
    if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(home) or path == home:
        raise ValueError('Installation must use a new directory below the remote home')
    if any(parent.is_symlink() for parent in [path, *path.parents]):
        raise ValueError('Symlink installation paths are not allowed')
    if path.exists():
        raise FileExistsError('Destination already exists; choose a new directory')
    return path


def install(payload):
    target = destination(payload['destination'])
    filename = payload['filename']
    if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl', filename):
        raise ValueError('Invalid wheel filename')
    content = base64.b64decode(payload['wheel'], validate=True)
    if len(content) > 16 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != payload['sha256']:
        raise ValueError('Wheel checksum mismatch')
    os.umask(0o077)
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    wheel = target / filename
    with wheel.open('xb') as stream:
        stream.write(content)
    environment = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'}}
    venv.EnvBuilder(with_pip=True).create(target / 'venv')
    python = target / 'venv' / 'bin' / 'python'
    with (target / 'install.log').open('xb') as log:
        result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                                 '--disable-pip-version-check', str(wheel)], env=environment,
                                stdout=log, stderr=log, timeout=120)
    if result.returncode:
        raise RuntimeError('Offline pip installation failed; install.log and files retained')
    # Initialize only a brand-new data directory. Never migrate an existing DB.
    root = Path(payload['root']).expanduser()
    initialized = False
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        environment['CSLURM_ROOT'] = str(root)
        subprocess.run([str(python), '-c', 'from cslurm.db import connect,init_db\n'
                         'with connect() as conn: init_db(conn)'], env=environment, check=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        initialized = True
    result = {'status': 'installed', 'python': str(python), 'destination': str(target),
              'sha256': payload['sha256'], 'root_initialized': initialized}
    with (target / 'installation.json').open('x') as stream:
        json.dump(result, stream)
    return result


def main():
    try:
        if sys.version_info < (3, 10):
            raise RuntimeError('Python 3.10 or newer is required')
        line = sys.stdin.buffer.readline(24 * 1024 * 1024 + 1)
        if len(line) > 24 * 1024 * 1024:
            raise ValueError('Installation payload too large')
        print(json.dumps(install(json.loads(line))), flush=True)
    except Exception as exc:
        # Exceptions here contain no credentials or provider response bodies.
        print(json.dumps({'status': 'failed', 'error': str(exc)}), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
