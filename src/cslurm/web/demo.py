"""Synthetic records for an isolated, non-operational web preview."""

import json
from datetime import datetime, timedelta, timezone

from cslurm.config import root_dir
from cslurm.db import run_write_transaction


def seed():
    now = datetime.now(timezone.utc)
    names = ["dmft_U3.0_Nk5000", "nrg_spectral_scan", "tensor_ground_state", "thermal_observables", "dmft_U2.5_Nk3000", "matlab_eigensolver", "python_postprocess", "julia_convergence"]
    states = ["RUNNING", "RUNNING", "PENDING", "COMPLETED", "COMPLETED", "FAILED", "COMPLETED", "TIMEOUT", "COMPLETED", "CANCELLED", "COMPLETED", "UNKNOWN"]

    def write(conn):
        for i in range(48):
            job_id = str(62084 - i)
            state = states[i % len(states)] if i < 24 else "COMPLETED"
            name = names[i % len(names)]
            folder = root_dir() / "example-runs" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            log = folder / f"slurm-{job_id}.out"
            content = f"[DEMO] Job {job_id}: {name}\nInitializing numerical solver\nNk = 5000, U = 3.0, T = 1e-5\n"
            if state == "FAILED":
                content += "Traceback (most recent call last):\n  File 'run.py', line 84, in solve\nRuntimeError: eigenvalue solver did not converge\n"
            else:
                content += "\n".join(f"iteration {n:03d}   residual = {0.12 / (n*n):.6e}" for n in range(1, 19))
                if state == "COMPLETED":
                    content += "\nConverged. Results saved to spectrum.dat\n"
            log.write_text(content)
            script = folder / "job.slurm"
            script.write_text("#!/bin/bash\n#SBATCH --partition=compute\npython3 run.py\n")
            submit = (now - timedelta(minutes=30+i*41)).isoformat()
            summary = {"one_line_summary": f"演示摘要：{name} 计算任务。扫描相互作用参数并求解自洽方程，记录谱函数与收敛过程。", "language": "Python", "entry_file": "run.py", "summary_confidence": 0.92, "data_source": "synthetic demo"}
            conn.execute(
                "insert into jobs (job_id, job_name, partition, user, state, exit_code, derived_exit_code, elapsed, max_rss, "
                "nodelist, nodes, alloc_cpus, submitted_at, updated_at, created_at, submit_cwd, effective_chdir, command, "
                "original_script_path, stdout_path, summary_json, completion_summary_json) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, name, "compute" if i % 4 else "cpu-long", "researcher", state, "1:0" if state == "FAILED" else "0:0",
                 "1:0" if state == "FAILED" else "0:0", "02:14:38" if state == "RUNNING" else "00:00:00" if state == "PENDING" else f"00:{12+i%40:02d}:26",
                 "3.24G" if state != "PENDING" else None, f"cpu{i%8+1:02d}" if state != "PENDING" else None, "1", 16,
                 submit, now.isoformat(), submit, str(folder), str(folder), "csbatch job.slurm", str(script), str(log), json.dumps(summary, ensure_ascii=False),
                 json.dumps({"human_summary": "演示完成摘要：程序退出码为 0，日志记录已收敛。", "data_source": "synthetic demo"}, ensure_ascii=False) if state == "COMPLETED" else None),
            )
            conn.execute("insert into job_events (job_id,event_time,event_type,raw_output) values (?,?,?,?)", (job_id, submit, "SUBMITTED", f"Submitted batch job {job_id}"))
            conn.execute("insert into job_events (job_id,event_time,event_type,raw_output) values (?,?,?,?)", (job_id, now.isoformat(), "STATE_CHANGED", "UNKNOWN -> " + state))
            conn.execute("insert into job_files (job_id,path,relpath,role,source,size) values (?,?,?,?,?,?)", (job_id, str(script), "job.slurm", "original_script", "submission", script.stat().st_size))
            conn.execute("insert into job_commands (job_id,kind,executable,argv,entry_file,source) values (?,?,?,?,?,?)", (job_id, "python", "python3", '["python3", "run.py"]', "run.py", "static"))
            if state in {"FAILED", "TIMEOUT", "COMPLETED"}:
                category = state if state != "COMPLETED" else "NONE"
                conn.execute("insert into job_analysis (job_id,created_at,slurm_state,semantic_status,failure_category) values (?,?,?,?,?)", (job_id, now.isoformat(), state, "normal" if state == "COMPLETED" else "hard_failed", category))
                conn.execute("insert into notifications (job_id,created_at,mode,status,category,title,body) values (?,?,?,?,?,?,?)", (job_id, now.isoformat(), "batch", "pending", category, f"[DEMO][{category}] Job {job_id}", "演示通知，未发送至飞书。"))

    run_write_transaction(write)
