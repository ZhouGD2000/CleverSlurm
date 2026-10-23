"""Native execution, independent of metadata parsing and database availability."""

import os
import signal
import subprocess
import threading
from pathlib import Path

from cslurm.config import command_path
from cslurm.shims import MANAGED_MARKER


def _is_tracking_entrypoint(path):
    try:
        with path.open('rb') as stream:
            prefix = stream.read(4096)
        return MANAGED_MARKER.encode() in prefix or b'from cslurm.cli.csbatch import' in prefix
    except OSError:
        return False


def executable() -> str:
    command = command_path('sbatch')
    if command != 'sbatch':
        return command
    # Resolve the native executable without changing PATH exported to the job.
    for directory in os.get_exec_path():
        candidate = Path(directory or '.') / 'sbatch'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            if not _is_tracking_entrypoint(candidate):
                return str(candidate.absolute())
    raise FileNotFoundError('Native sbatch not found; set CSLURM_SBATCH to its absolute path')


def run(argv: list[str], *, stream: bool = False) -> subprocess.CompletedProcess[bytes]:
    command = [executable(), *argv]
    # Inherited export-file descriptors and stdin belong to Slurm, not the proxy.
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=None if stream else subprocess.PIPE,
                               close_fds=False, restore_signals=True)
    previous = {}
    if threading.current_thread() is threading.main_thread():
        def forward(signum, frame):
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
            handler = signal.getsignal(signum)
            if handler != signal.SIG_IGN:
                previous[signum] = handler
                signal.signal(signum, forward)
    try:
        if not stream:
            output, error = process.communicate()
        else:
            observed = bytearray()
            with process.stdout:
                while True:
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    # Bound observation, not forwarding: verbose/plugin output is unlimited.
                    observed.extend(chunk[:max(0, 1024 * 1024 - len(observed))])
                    try:
                        remaining = memoryview(chunk)
                        while remaining:
                            remaining = remaining[os.write(1, remaining):]
                    except OSError:
                        # Closing the read end gives the native writer its normal EPIPE.
                        break
            process.wait()
            output, error = bytes(observed), b''
        return subprocess.CompletedProcess(command, process.returncode, output, error)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def exit_like_native(returncode: int) -> None:
    if returncode < 0:
        signum = -returncode
        if signum not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    raise SystemExit(returncode)
