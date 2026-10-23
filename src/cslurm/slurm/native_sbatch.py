"""Native execution, independent of metadata parsing and database availability."""

import os
import subprocess
from cslurm.slurm.native import executable as resolve_executable, exit_like_native, relay_signals


def executable() -> str:
    return resolve_executable('sbatch')


def run(argv: list[str], *, stream: bool = False) -> subprocess.CompletedProcess[bytes]:
    command = [executable(), *argv]
    # Inherited export-file descriptors and stdin belong to Slurm, not the proxy.
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=None if stream else subprocess.PIPE,
                               close_fds=False, restore_signals=True)
    with relay_signals(process):
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
