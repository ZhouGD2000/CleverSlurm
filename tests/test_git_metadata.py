import sqlite3
import subprocess

from conftest import write_executable


def test_csbatch_collects_git_commit_status_and_diff(isolated_home, fake_bin, tmp_path, monkeypatch):
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    tracked = repo / "run.py"
    tracked.write_text("print('v1')\n")
    subprocess.run(["git", "add", "run.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, stdout=subprocess.PIPE)
    tracked.write_text("print('v2')\n")
    script = repo / "job.slurm"
    script.write_text("#!/bin/bash\npython run.py\n")
    monkeypatch.chdir(repo)

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select git_commit, git_dirty from jobs where job_id = '123456'").fetchone()

    assert row[0]
    assert row[1] == 1
    assert "print('v2')" in (isolated_home / "jobs" / "123456" / "git.diff").read_text()
    assert " M run.py" in (isolated_home / "jobs" / "123456" / "git_status.txt").read_text()
