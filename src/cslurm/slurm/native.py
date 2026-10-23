"""Native Slurm execution without option rewriting or tracking dependencies."""

import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from cslurm.config import command_path
from cslurm.shims import MANAGED_MARKER


def _is_tracking_entrypoint(path, name):
    try:
        with path.open('rb') as stream:
            prefix = stream.read(4096)
        return (MANAGED_MARKER.encode() in prefix
                or f'from cslurm.cli.c{name} import'.encode() in prefix)
    except OSError:
        return False


def executable(name):
    command = command_path(name)
    if command != name:
        return command
    for directory in os.get_exec_path():
        candidate = Path(directory or '.') / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            if not _is_tracking_entrypoint(candidate, name):
                return str(candidate.absolute())
    raise FileNotFoundError(f'Native {name} not found; set CSLURM_{name.upper()} to its absolute path')


def _foreground_terminal():
    try:
        return os.tcgetpgrp(0) == os.getpgrp()
    except OSError:
        return False


@contextmanager
def relay_signals(process, *, inherited_terminal=False):
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def forward(signum, frame):
            # The terminal already sent these to every foreground group member.
            # Relaying Ctrl+C would trigger srun's double-interrupt abort behavior.
            if inherited_terminal and signum in (signal.SIGINT, signal.SIGQUIT) and _foreground_terminal():
                return
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT,
                       signal.SIGUSR1, signal.SIGUSR2):
            handler = signal.getsignal(signum)
            if handler != signal.SIG_IGN:
                previous[signum] = handler
                signal.signal(signum, forward)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run(name, argv, *, capture_output=False):
    command = [executable(name), *argv]
    # CLI inherits all three streams, including controlling-terminal identity.
    process = subprocess.Popen(command, close_fds=False, restore_signals=True,
                               stdout=subprocess.PIPE if capture_output else None,
                               stderr=subprocess.PIPE if capture_output else None)
    with relay_signals(process, inherited_terminal=True):
        output, error = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode,
                                       output.decode(errors='replace') if output is not None else None,
                                       error.decode(errors='replace') if error is not None else None)


def exec_native(name, argv):
    path = executable(name)
    # Python ignores SIGPIPE on startup; a shell-launched native utility does not.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    os.execv(path, [path, *argv])


def exit_like_native(returncode):
    if returncode < 0:
        signum = -returncode
        if signum not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    raise SystemExit(returncode)
