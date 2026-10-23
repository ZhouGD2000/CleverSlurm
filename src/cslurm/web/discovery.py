"""Bounded SSH config enumeration and explicitly requested environment discovery."""

import glob
import itertools
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import time

from cslurm.web.remotes import RemoteError, ssh_transport

ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
SSH_DISCOVERY_TIMEOUT = 50


def ssh_aliases(config=None):
    config = Path(config) if config is not None else Path.home() / '.ssh/config'
    seen, aliases, warnings = set(), set(), []
    total = 0

    def read(path, depth=0):
        nonlocal total
        try:
            path = path.resolve()
            if path.exists() and not path.is_file():
                warnings.append('An SSH config path is not a regular file')
                return
        except (OSError, RuntimeError):
            warnings.append('An SSH config path could not be resolved')
            return
        if path in seen or depth > 8 or len(seen) >= 64:
            warnings.append('SSH Include cycle or traversal limit; some aliases may be omitted')
            return
        seen.add(path)
        try:
            with path.open('rb') as stream:
                content = stream.read(262145)
            total += len(content)
            if len(content) > 262144 or total > 1048576:
                warnings.append('SSH config size limit; some aliases may be omitted')
                return
            content = content.decode('utf-8')
        except FileNotFoundError:
            return
        except (OSError, UnicodeError):
            warnings.append('An SSH config file could not be read')
            return
        for line in content.splitlines():
            # SSH accepts both "Host name" and "Host=name". Never evaluate
            # Match/ProxyCommand or expose credentials/config contents.
            line = re.sub(r'^(\s*[A-Za-z]+)\s*=\s*', r'\1 ', line)
            try:
                parts = shlex.split(line, comments=True)
            except ValueError:
                warnings.append('An SSH config line could not be parsed')
                continue
            if not parts:
                continue
            if parts[0].lower() == 'host':
                aliases.update(p for p in parts[1:] if ALIAS.fullmatch(p))
            elif parts[0].lower() == 'include':
                for pattern in parts[1:]:
                    target = Path(pattern).expanduser()
                    if not target.is_absolute():
                        target = config.parent / target
                    matches = list(itertools.islice(glob.iglob(str(target)), 65))
                    if len(matches) > 64:
                        warnings.append('SSH Include match limit; some aliases may be omitted')
                    for match in sorted(matches[:64]):
                        if len(seen) >= 64:
                            warnings.append('SSH Include file limit; some aliases may be omitted')
                            break
                        read(Path(match), depth + 1)
    read(config)
    return {'items': [{'alias': alias} for alias in sorted(aliases)],
            'warnings': sorted(set(warnings)), 'config': str(config)}


def discovery_command(alias):
    from cslurm.web import remote_discovery
    if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
        raise ValueError('Use a concrete SSH config alias')
    source = Path(remote_discovery.__file__).read_text()
    # Bootstrap independently of the user's non-interactive shell PATH.
    script = '''
n=0
for p in "$(command -v python3)" "$(command -v python)" \\
 "$HOME"/anaconda3/bin/python "$HOME"/miniconda3/bin/python \\
 "$HOME"/miniforge3/bin/python "$HOME"/mambaforge/bin/python \\
 "$HOME"/software/anaconda3/bin/python "$HOME"/software/miniconda3/bin/python \\
 "$HOME"/.local/share/cleverslurm/venv/bin/python \\
 "$HOME"/.local/share/uv/tools/cleverslurm/bin/python \\
 "$HOME"/.virtualenvs/*/bin/python "$HOME"/software/cleverslurm/.venv/bin/python; do
 [ -x "$p" ] || continue
 n=$((n+1)); [ "$n" -le 24 ] || break
 if "$p" -B -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
  exec "$p" -B -c SOURCE
 fi
done
printf '%s\\n' '{"status":"no_python","environments":[],"warnings":["No Python 3.8+ bootstrap found; specify an interpreter manually"]}'
'''.replace('SOURCE', shlex.quote(source))
    return ssh_transport(alias, 'exec /bin/sh -c ' + shlex.quote(script))


def discover(alias):
    command = discovery_command(alias)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + SSH_DISCOVERY_TIMEOUT
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], max(remaining, 0))[0]:
                raise RemoteError('SSH environment detection timed out; installation status is unknown')
            part = os.read(process.stdout.fileno(), 65536)
            if not part:
                break
            output.extend(part)
            if len(output) > 262144:
                raise RemoteError('SSH discovery response too large')
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
        if process.returncode:
            raise RemoteError('SSH detection failed; check alias, known_hosts and key/agent access')
        try:
            result = json.loads(output)
            if (not isinstance(result, dict) or result.get('status') not in {'ok', 'no_python'}
                    or not isinstance(result.get('environments'), list)):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise RemoteError('Invalid SSH detection response; check shell startup output') from None
        return {**result, 'alias': alias}
    except subprocess.TimeoutExpired:
        raise RemoteError('SSH environment detection timed out; installation status is unknown') from None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
