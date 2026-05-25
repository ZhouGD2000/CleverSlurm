import json
import subprocess
import sys

from cslurm.db import connect, init_db
from cslurm.cli.cjobs import (
    list_commands,
    list_events,
    list_files,
    list_notifications,
    queue_jobs,
    recent_jobs,
    show_job,
    show_logs,
    show_summary,
)


def test_cjobs_show_returns_job_metadata(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, exit_code, created_at, updated_at) "
            "values ('123456', '2026-05-17T01:02:03', '/work/project', 'csbatch job.slurm', 'test-job', 'COMPLETED', '0:0', 't', 't')"
        )

    text = show_job("123456")

    assert "Job 123456" in text
    assert "state: COMPLETED" in text
    assert "job_name: test-job" in text
    assert "command: csbatch job.slurm" in text


def test_cjobs_module_entrypoint_runs_main(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, exit_code, created_at, updated_at) "
            "values ('123456', '2026-05-17T01:02:03', '/work/project', 'csbatch job.slurm', 'test-job', 'COMPLETED', '0:0', 't', 't')"
        )

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.cli.cjobs", "show", "123456"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "Job 123456" in result.stdout
    assert "state: COMPLETED" in result.stdout


def test_cjobs_queue_cli_runs_main(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, state, created_at, updated_at
            ) values ('123456', '2026-05-17T01:02:03', 'test-job', 'zgd', 'CPU2', 'RUNNING', 't', 't')
            """
        )

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.cli.cjobs", "queue"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "JOBID" in result.stdout
    assert "123456" in result.stdout
    assert "CPU2" in result.stdout
    assert "R" in result.stdout


def test_cjobs_summary_reads_stored_submission_summary_without_ai(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, summary_json, created_at, updated_at) values ('123456', ?, 't', 't')",
            (json.dumps({"job_id": "123456", "one_line_summary": "Run a smoke job."}),),
        )

    text = show_summary("123456")

    assert '"one_line_summary": "Run a smoke job."' in text
    assert '"job_id": "123456"' in text


def test_cjobs_summary_reads_stored_completion_summary(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, completion_summary_json, created_at, updated_at) values ('123456', ?, 't', 't')",
            (json.dumps({"job_id": "123456", "completion_status": "COMPLETED"}),),
        )

    text = show_summary("123456", completion=True)

    assert '"completion_status": "COMPLETED"' in text


def test_cjobs_summary_reports_missing_summary(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute("insert into jobs (job_id, created_at, updated_at) values ('123456', 't', 't')")

    assert show_summary("123456") == "No submission summary recorded for job 123456"
    assert show_summary("123456", completion=True) == "No completion summary recorded for job 123456"


def test_cjobs_summary_cli_supports_completion_flag(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, completion_summary_json, created_at, updated_at) values ('123456', ?, 't', 't')",
            (json.dumps({"completion_status": "FAILED"}),),
        )

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.cli.cjobs", "summary", "123456", "--completion"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert '"completion_status": "FAILED"' in result.stdout


def test_cjobs_recent_uses_sacct_like_columns(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, account, state,
              exit_code, elapsed, max_rss, nodes, nodelist, reason, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123456",
                "2026-05-17T01:02:03",
                "test-job",
                "zgd",
                "CPU2",
                "acct",
                "RUNNING",
                "0:0",
                "00:01:02",
                "2G",
                "2",
                "node[01-02]",
                None,
                "t",
                "t",
            ),
        )
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, account, state,
              exit_code, elapsed, max_rss, nodes, nodelist, reason, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123457",
                "2026-05-17T01:03:03",
                "pending",
                "zgd",
                "CPU2",
                "acct",
                "PENDING",
                None,
                None,
                None,
                "1",
                None,
                "Priority",
                "t",
                "t",
            ),
        )

    lines = recent_jobs(10).splitlines()

    assert lines[0].split() == [
        "JobID",
        "JobName",
        "Partition",
        "Account",
        "State",
        "ExitCode",
        "Elapsed",
        "MaxRSS",
        "NodeList",
        "Submit",
    ]
    assert "123457" in lines[1]
    assert "pending" in lines[1]
    assert "CPU2" in lines[1]
    assert "acct" in lines[1]
    assert "PENDING" in lines[1]
    assert "123456" in lines[2]
    assert "RUNNING" in lines[2]
    assert "0:0" in lines[2]
    assert "00:01:02" in lines[2]
    assert "2G" in lines[2]
    assert "node[01-02]" in lines[2]


def test_cjobs_queue_uses_squeue_like_columns(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, state, elapsed,
              nodes, nodelist, reason, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123456",
                "2026-05-17T01:02:03",
                "test-job",
                "zgd",
                "CPU2",
                "RUNNING",
                "00:01:02",
                "2",
                "node[01-02]",
                None,
                "t",
                "t",
            ),
        )
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, state, elapsed,
              nodes, nodelist, reason, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123457",
                "2026-05-17T01:03:03",
                "pending",
                "zgd",
                "CPU2",
                "PENDING",
                None,
                "1",
                None,
                "Priority",
                "t",
                "t",
            ),
        )
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, job_name, user, partition, state, elapsed,
              nodes, nodelist, reason, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123458",
                "2026-05-17T01:04:03",
                "done",
                "zgd",
                "CPU2",
                "COMPLETED",
                "00:00:10",
                "1",
                "node03",
                None,
                "t",
                "t",
            ),
        )

    lines = queue_jobs(10).splitlines()

    assert lines[0].split() == ["JOBID", "PARTITION", "NAME", "USER", "ST", "TIME", "NODES", "NODELIST(REASON)"]
    assert "123457" in lines[1]
    assert "CPU2" in lines[1]
    assert "pending" in lines[1]
    assert "PD" in lines[1]
    assert "0:00" in lines[1]
    assert "(Priority)" in lines[1]
    assert "123456" in lines[2]
    assert "R" in lines[2]
    assert "00:01:02" in lines[2]
    assert "node[01-02]" in lines[2]
    assert "123458" not in "\n".join(lines)


def test_cjobs_recent_no_header(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, state, created_at, updated_at) "
            "values ('123456', '2026-05-17T01:02:03', 'COMPLETED', 't', 't')"
        )

    text = recent_jobs(10, no_header=True)

    assert "JobID" not in text
    assert "123456" in text
    assert "COMPLETED" in text


def test_cjobs_recent_falls_back_to_saved_script_for_partition_and_name(isolated_home, tmp_path):
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "#SBATCH --job-name=from-script\n"
        "#SBATCH -p CPU2\n"
        "hostname\n"
    )
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, original_script_path, state, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            ("123456", "2026-05-17T01:02:03", str(script), "RUNNING", "t", "t"),
        )

    text = recent_jobs(10)

    assert "CPU2" in text
    assert "from-script" in text
    assert "RUNNING" in text


def test_cjobs_recent_falls_back_to_recorded_command_for_partition_and_name(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, command, state, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?)
            """,
            (
                "123456",
                "2026-05-17T01:02:03",
                "csbatch --job-name from-command -p CPU2 job.slurm",
                "PENDING",
                "t",
                "t",
            ),
        )

    text = recent_jobs(10)

    assert "CPU2" in text
    assert "from-command" in text
    assert "PENDING" in text


def test_cjobs_events_files_and_commands_return_tables(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, command, note, raw_output) "
            "values ('123456', '2026-05-17T01:02:03', 'CANCEL_REQUESTED', 'scancel 123456', 'wrong U', 'cancelled')"
        )
        conn.execute(
            "insert into job_files (job_id, path, relpath, sha256, size, role, source, copied, confidence) "
            "values ('123456', '/work/run.jl', 'run.jl', 'abc', 12, 'entry_file', 'runtime-command', 1, 1.0)"
        )
        conn.execute(
            "insert into job_commands (job_id, time, hostname, cwd, kind, executable, argv, entry_file, source) "
            "values ('123456', '2026-05-17T01:02:04', 'node001', '/work', 'julia', '/bin/julia', '[\"run.jl\"]', 'run.jl', 'runtime-wrapper')"
        )

    assert "CANCEL_REQUESTED" in list_events("123456")
    assert "wrong U" in list_events("123456")
    assert "run.jl" in list_files("123456")
    assert "entry_file" in list_files("123456")
    assert "julia" in list_commands("123456")
    assert "/bin/julia" in list_commands("123456")
    assert "runtime-wrapper" in list_commands("123456")


def test_cjobs_files_falls_back_to_copied_script_paths(isolated_home, tmp_path):
    original = tmp_path / "job.slurm"
    copied = tmp_path / "instrumented.slurm"
    original.write_text("#!/bin/bash\nhostname\n")
    copied.write_text("#!/bin/bash\nhostname\n")
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, original_script_path, copied_script_path, created_at, updated_at) "
            "values ('123456', ?, ?, 't', 't')",
            (str(original), str(copied)),
        )

    text = list_files("123456")

    assert "original.slurm" in text
    assert "instrumented.slurm" in text
    assert str(original) in text
    assert str(copied) in text


def test_cjobs_empty_tables_return_explanatory_messages(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, created_at, updated_at) values ('123456', 't', 't')"
        )

    assert list_files("123456") == "No files recorded for job 123456"
    assert list_commands("123456") == "No commands recorded for job 123456"
    assert list_notifications("123456") == "No notifications recorded for job 123456"
    assert list_notifications() == "No notifications recorded"


def test_cjobs_notifications_returns_notification_rows(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into notifications (job_id, mode, status, severity, category, title) "
            "values ('123456', 'immediate', 'sent', 'high', 'FAILED', '[CleverSlurm][FAILED] Job 123456')"
        )

    text = list_notifications("123456")

    assert "123456" in text
    assert "immediate" in text
    assert "FAILED" in text


def test_cjobs_logs_tails_recorded_stdout_and_stderr(isolated_home, tmp_path):
    stdout = tmp_path / "job.out"
    stderr = tmp_path / "job.err"
    stdout.write_text("out1\nout2\nout3\n")
    stderr.write_text("err1\nerr2\n")
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, stdout_path, stderr_path, created_at, updated_at) "
            "values ('123456', ?, ?, 't', 't')",
            (str(stdout), str(stderr)),
        )

    text = show_logs("123456", tail=2)

    assert "== stdout ==" in text
    assert "out2\nout3" in text
    assert "== stderr ==" in text
    assert "err1\nerr2" in text


def test_cjobs_logs_infers_default_slurm_output_when_paths_missing(isolated_home, tmp_path):
    default_log = tmp_path / "slurm-123456.out"
    default_log.write_text("line1\nline2\nline3\n")
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submit_cwd, created_at, updated_at) values ('123456', ?, 't', 't')",
            (str(tmp_path),),
        )

    text = show_logs("123456", tail=2)

    assert "== stdout (inferred default slurm log) ==" in text
    assert "line2\nline3" in text
