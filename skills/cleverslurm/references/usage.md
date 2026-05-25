# CleverSlurm Usage

## Install As A Normal CLI Package

From a checkout:

```bash
python3 -m pip install -e .
```

or with uv:

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

The package installs `csbatch`, `csrun`, `cscancel`, `cjobs`, `ctrack`, `csummarize`, `cnotify`, and `cshim`.

## Use As A Codex Skill

Copy or install `skills/cleverslurm/` into the user's Codex skills directory. The skill does not install the CLI package. For full functionality, also install the Python package from the repository:

```bash
git clone git@github.com:ZhouGD2000/CleverSlurm.git
cd CleverSlurm
python3 -m pip install -e .
```

## Job Submission

```bash
csbatch job.slurm
csbatch -p CPU2 --time=00:01:00 job.slurm arg1 arg2
csbatch --wrap "hostname && python3 script.py"
```

`csbatch` records the job, copies scripts, records git metadata, records stdout/stderr paths, queues static analysis, and queues AI submission summaries when enabled.

## Existing Scripts That Call Slurm Names

Aliases only affect interactive shells. For Python `subprocess.run(["sbatch", ...])`, install shims:

```bash
cshim install
cshim status
```

Use `cshim remove` before uninstalling.

## Query Jobs

```bash
cjobs recent
cjobs queue
cjobs show JOBID
cjobs events JOBID
cjobs files JOBID
cjobs commands JOBID
cjobs logs JOBID --tail 100
cjobs notifications JOBID
cjobs summary JOBID
cjobs summary JOBID --completion
cjobs ask "最近完成了什么任务？都是些什么工作？"
```

`cjobs recent` is sacct-like and includes recorded history. `cjobs queue` is squeue-like and lists active or not-yet-terminal tracked jobs.

## Automatic Tracking

```bash
ctrack
ctrack auto on
ctrack auto status
ctrack auto restart
ctrack auto off
```

For source checkouts and cron, prefer explicit paths:

```bash
ctrack auto on --repo /path/to/CleverSlurm --python /path/to/python3
```
