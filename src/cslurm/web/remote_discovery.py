"""Self-contained read-only probe sent over SSH, never installed remotely."""

import glob
import itertools
import json
import os
from pathlib import Path
import shlex
import shutil
import select
import subprocess
import sys
import time


PROBE = r'''
import importlib.util, json, os, sys
from importlib import metadata
spec = importlib.util.find_spec('cslurm')
package = spec.origin if spec else None
try:
    version = metadata.version('CleverSlurm') if spec else None
except metadata.PackageNotFoundError:
    version = 'source'
locations = list(spec.submodule_search_locations or []) if spec else []
web = any(os.path.isfile(os.path.join(p, 'web', 'server.py')) for p in locations)
root = os.environ.get('CSLURM_ROOT') or '~/.cslurm'
root = os.path.abspath(os.path.expanduser(root))
print(json.dumps(dict(python=sys.executable, prefix=sys.prefix,
    python_version='.'.join(map(str, sys.version_info[:3])),
    supported=sys.version_info >= (3, 10), installed=bool(spec), package=package,
    version=version, web=web, root=root,
    root_source='environment' if os.environ.get('CSLURM_ROOT') else 'default')))
'''


def candidates():
    home = Path.home()
    found = []

    def add(path):
        if not path or len(found) >= 48:
            return
        path = os.path.abspath(os.path.expanduser(str(path)))
        if path not in found and os.path.isfile(path) and os.access(path, os.X_OK):
            found.append(path)

    add(sys.executable)
    for name in ('cweb', 'cjobs', 'csbatch'):
        command = shutil.which(name)
        if not command:
            continue
        add(Path(command).parent / 'python')
        add(Path(command).parent / 'python3')
        try:
            with open(command, 'rb') as stream:
                line = stream.readline(4096).decode('utf-8')
            if line.startswith('#!'):
                words = shlex.split(line[2:])
                if words and os.path.basename(words[0]) == 'env':
                    words = [shutil.which(words[1])] if len(words) == 2 else []
                if words and words[0] and 'python' in os.path.basename(words[0]).lower():
                    add(words[0])
        except (OSError, ValueError, UnicodeError):
            pass
    for name in ('python3', 'python'):
        add(shutil.which(name))
    for base in ('anaconda3', 'miniconda3', 'miniforge3', 'mambaforge',
                 'software/anaconda3', 'software/miniconda3', 'software/miniforge3'):
        add(home / base / 'bin/python')
        for path in itertools.islice(glob.iglob(str(home / base / 'envs/*/bin/python')), 16):
            add(path)
    for pattern in ('.virtualenvs/*/bin/python', '.local/share/uv/tools/cleverslurm/bin/python',
                    '.local/share/cleverslurm/venv/bin/python', 'software/cleverslurm/.venv/bin/python'):
        for path in itertools.islice(glob.iglob(str(home / pattern)), 16):
            add(path)
    try:
        with (home / '.conda/environments.txt').open() as stream:
            for line in itertools.islice(stream, 32):
                if len(line) < 4096:
                    add(Path(line.strip()) / 'bin/python')
    except OSError:
        pass
    return found


def run_probe(python, timeout):
    args = [python, '-B', '-c', PROBE]
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    deadline, output = time.monotonic() + timeout, bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], max(remaining, 0))[0]:
                raise subprocess.TimeoutExpired(args, timeout)
            part = os.read(process.stdout.fileno(), 65536)
            if not part:
                break
            output.extend(part)
            if len(output) > 65536:
                raise ValueError('Python probe output too large')
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return subprocess.CompletedProcess(args, process.returncode, output.decode('utf-8'))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


def inspect():
    environments, warnings, seen = [], [], set()
    deadline = time.monotonic() + 25
    for python in candidates():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            warnings.append('Detection time limit reached; some environments were not checked')
            break
        try:
            result = run_probe(python, min(3, remaining))
            item = json.loads(result.stdout) if result.returncode == 0 else None
            if not isinstance(item, dict) or not isinstance(item.get('python'), str):
                raise ValueError()
            identity = (item.pop('prefix'), item['python_version'], item['package'])
            if identity not in seen:
                environments.append(item)
                seen.add(identity)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            warnings.append('A Python candidate failed or timed out; discovery is not exhaustive')
    environments.sort(key=lambda i: (not (i['installed'] and i['supported'] and i['web']),
                                     not i['installed'], not i['supported']))
    return {'status': 'ok', 'environments': environments, 'warnings': sorted(set(warnings))}


if __name__ == '__main__':
    print(json.dumps(inspect()))
