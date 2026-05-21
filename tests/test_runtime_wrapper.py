import json
import os
import subprocess
import sys

from conftest import write_executable


def test_runtime_wrapper_writes_command_log_and_execs_real_program(tmp_path):
    real_julia = write_executable(
        tmp_path / "real-julia",
        "#!/bin/sh\nprintf 'real:%s\\n' \"$1\"\n",
    )
    log_dir = tmp_path / "runtime"
    env = os.environ.copy()
    env.update({
        "CSLURM_LOG_DIR": str(log_dir),
        "CSLURM_JOB_ID": "123456",
        "CSLURM_REAL_JULIA": str(real_julia),
    })

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.runtime.wrapper", "julia", "script.jl", "--U", "4"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout == "real:script.jl\n"
    record = json.loads((log_dir / "commands.log").read_text().strip())
    assert record["job_id"] == "123456"
    assert record["kind"] == "julia"
    assert record["argv"] == ["script.jl", "--U", "4"]
    assert record["entry_file"] == "script.jl"
    assert record["executable"] == str(real_julia)
