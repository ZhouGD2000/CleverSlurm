# CleverSlurm Job Tracking System: Coding Agent Implementation Guide

## 0. Objective

Build a server-side job-tracking system for Slurm workflows.

The system should automatically record what the user submitted, what code actually ran, where it ran, what code/config files were relevant, how the job progressed, whether it completed successfully, and what the job was scientifically/computationally intended to do.

The core principle is:

> Facts are collected by deterministic instrumentation.  
> AI is used for semantic summarization, comparison, diagnosis, and question answering.

This should be implemented as an external CLI/daemon/MCP-capable system, not merely as a ChatGPT/Codex skill.

A skill may later be added to teach Codex how to call the local tools, but the persistent recording, Slurm monitoring, file snapshotting, and database logic must live in the server-side system.

---

## 1. High-Level Architecture

Recommended architecture:

```text
csbatch / csrun / csalloc / cscancel / cupdate
        |
        v
deterministic collector
        |
        |-- Slurm metadata
        |-- runtime command log
        |-- git commit/status/diff
        |-- static dependency graph
        |-- environment snapshot
        |-- content-addressed source snapshot
        v
SQLite + FTS
        |
        v
LLM summarizer
        |
        v
tracker daemon: squeue + sacct + sstat + logs
        |
        v
cjobs CLI / optional MCP server / optional Codex skill
```

The system should separate four responsibilities:

1. **Submission wrappers**: intercept job creation and state-changing operations.
2. **Collectors**: record facts such as commands, paths, git diffs, dependencies, environment, Slurm metadata.
3. **Trackers**: periodically poll Slurm state and job logs.
4. **AI summarizers/query agents**: produce structured summaries and answer user questions from the database.

---

## 2. Commands to Support

### 2.1 Must Support: Submission and Execution

Implement wrappers:

```text
csbatch      wraps sbatch
csalloc      wraps salloc
csrun        wraps srun
```

Roles:

```text
sbatch:
  Main batch submission path.

salloc:
  Interactive allocation path. Must be tracked because many debugging/calibration jobs are run interactively.

srun:
  Can either start a job step inside an existing allocation or directly launch a Slurm-managed execution.
  Must be recorded as either:
    - job step under an existing job
    - standalone execution if launched directly
```

Do not assume that one Slurm job corresponds to one program. A batch script may execute multiple commands:

```bash
julia preprocess.jl
srun julia main.jl --U 4 --L 12
python postprocess.py
```

The database must support multiple commands/steps per job.

---

### 2.2 Must Support: State-Changing Commands

Implement wrappers or safe frontends:

```text
cscancel     wraps scancel
chold        wraps scontrol hold
crelease     wraps scontrol release
crequeue     wraps scontrol requeue
cupdate      wraps selected scontrol update operations
```

Recommended operations to record:

```text
scancel
scontrol hold
scontrol release
scontrol requeue
scontrol update
scontrol suspend
scontrol resume
```

For each state-changing command, write an event record with:

```text
job_id
event_time
event_type
command
cwd
raw_output
optional user note
```

Support user notes:

```bash
cscancel 123456 --note "Parameter U was wrong; cancel and rerun."
crequeue 123456 --note "Requeue after node failure."
chold 123456 --note "Wait for previous DMFT run to finish."
```

These notes are important for later AI explanations.

---

### 2.3 Query Commands Used by Tracker

The user does not need to call these manually through wrappers. The tracker daemon should call them periodically:

```text
squeue
sacct
sstat
seff
scontrol show job
scontrol show node
sinfo
```

Optional, for more advanced queue explanations:

```text
sprio
sshare
sacctmgr show qos
sacctmgr show assoc
sdiag
```

Purpose:

```text
squeue:
  Active PENDING/RUNNING jobs.

sacct:
  Accounting state, exit code, elapsed time, MaxRSS, node list, submit/start/end times.

sstat:
  Real-time resource information for running job steps.

seff:
  Post-run efficiency information if available on the cluster.

scontrol show job:
  Detailed job state, dependencies, reason, workdir, command, stdout/stderr.

sinfo:
  Partition/node state context, useful for explaining pending reasons.
```

---

## 3. Core Design Principle: Do Not Let AI Decide Facts

The system must not rely on AI to determine:

```text
actual executable path
actual working directory
actual argv
actual Slurm job id
actual stdout/stderr paths
actual git commit
actual modified files
actual job state
actual exit code
```

These must be collected deterministically.

AI may be used to determine or summarize:

```text
scientific/computational purpose
important parameters
which code changes appear most relevant
likely expected outputs
why a job failed, based on logs and state
which previous jobs are similar
whether dependency analysis may be incomplete
human-readable summaries
```

Whenever AI suggests a file or interpretation, mark the source as `model-suggested` and attach confidence.

---

## 4. Runtime Command Log

### 4.1 Meaning

A **runtime command log** is a factual record of the commands actually executed during the job.

It should record:

```text
time
job_id
step_id if available
hostname
cwd
command kind: srun / julia / matlab / python / bash / other
resolved executable path
argv
entry file if identifiable
environment indicators
```

Example:

```text
time=2026-05-14T02:31:11
job_id=123456
hostname=node012
cwd=/work/project/DMFT
kind=julia
executable=/opt/julia-1.11.5/bin/julia
argv=julia --project=. run_dmft.jl --U 4 --mixing broyden
entry_file=/work/project/DMFT/run_dmft.jl
```

This log answers:

```text
What command actually ran?
Where did it run?
Which executable was used?
What arguments were passed?
Which entry file was launched?
Was it launched through srun?
Which node executed it?
```

It does not by itself answer which files were read by the program. That is handled by static dependency analysis and optional file-access tracing.

---

### 4.2 How to Implement Runtime Command Log

Use an instrumented Slurm script and lightweight executable wrappers.

`csbatch job.slurm` should:

```text
1. Preserve the original job.slurm.
2. Parse #SBATCH options.
3. Generate an instrumented temporary script.
4. Inject tracking environment variables.
5. Prepend ~/.cslurm/wrappers to PATH.
6. Submit the instrumented script via real /usr/bin/sbatch --parsable.
7. Store job_id and initial metadata.
```

Injected environment:

```bash
export CSLURM_JOB_ID="${SLURM_JOB_ID:-unknown}"
export CSLURM_SUBMIT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
export CSLURM_LOG_DIR="$HOME/.cslurm/jobs/${SLURM_JOB_ID}/runtime"
export PATH="$HOME/.cslurm/wrappers:$PATH"
mkdir -p "$CSLURM_LOG_DIR"
```

Wrappers to provide:

```text
~/.cslurm/wrappers/julia
~/.cslurm/wrappers/python
~/.cslurm/wrappers/matlab
~/.cslurm/wrappers/srun
~/.cslurm/wrappers/bash
```

Each wrapper should:

```text
1. Resolve the real executable.
2. Append command metadata to commands.log.
3. Exec the real executable with original arguments.
```

Important: prevent recursive self-calls. Either resolve the real executable before modifying PATH, or store paths in environment variables such as:

```bash
CSLURM_REAL_JULIA=/opt/julia-1.11.5/bin/julia
CSLURM_REAL_PYTHON=/opt/conda/envs/env/bin/python
CSLURM_REAL_MATLAB=/usr/local/MATLAB/R2024b/bin/matlab
CSLURM_REAL_SRUN=/usr/bin/srun
```

---

## 5. Static Dependency Analysis

Runtime command logging identifies entry commands. Static dependency analysis identifies relevant source/config files from entry files.

### 5.1 Julia

For a Julia command such as:

```bash
julia --project=. run_dmft.jl --U 4
```

Collect:

```text
entry file:
  run_dmft.jl

project files:
  Project.toml
  Manifest.toml

included local source files:
  include("...")
  include(joinpath(...)) when statically resolvable

local dev/path packages:
  packages in Manifest.toml with path = "..."
  packages developed via Pkg.develop

git metadata for current repo and local path packages:
  git rev-parse HEAD
  git status --porcelain
  git diff
  untracked relevant files

do not copy:
  normal registry package source code
  ~/.julia/compiled
  package caches
```

If `include("models/$(model_name).jl")` cannot be statically resolved, mark dependency confidence lower and optionally recommend runtime file tracing.

---

### 5.2 MATLAB

For MATLAB commands such as:

```bash
matlab -nodisplay -r "run('NRG/example_scripts/Ex_SIAM.m'); exit"
matlab -batch "main_script"
```

Collect:

```text
entry script/function
startup.m if relevant
addpath/genpath effects if statically visible
local .m files likely called from entry file
local project directories on MATLAB path
MATLAB version and toolbox version via ver
```

Do not copy MATLAB system toolboxes.

MATLAB dependency analysis is inherently less reliable than Julia due to path-based resolution and dynamic calls. Mark confidence accordingly.

---

### 5.3 Python

For Python commands such as:

```bash
python train.py --config configs/run1.yaml
```

Collect:

```text
entry file
local imports inside current repo
requirements.txt
pyproject.toml
setup.cfg
environment.yml
editable installs / local packages
config files passed via argv
```

Do not copy full `site-packages` unless a package is an editable/local development package.

---

### 5.4 Shell

For shell scripts:

```text
record source xxx.sh
record bash script invoked by local relative path
record module list
record conda environment
record PATH
record which julia/python/matlab
record environment variables relevant to execution
```

---

## 6. Optional Runtime File Tracing

Static analysis can miss dynamic dependencies such as:

```julia
include("models/$(model_name).jl")
```

or

```matlab
run(sprintf('cases/%s/main.m', case_name))
```

Provide optional modes:

```text
default:
  runtime command log + static dependency analysis

accurate:
  runtime command log + static analysis + short initial file-access trace

debug/reproduce:
  full file-access trace for important or hard-to-reproduce jobs
```

Possible implementation:

```bash
csbatch --trace-files job.slurm
```

Use `strace` on Linux if available:

```bash
strace -f -e trace=openat,openat2,stat,statx,execve -o filetrace.log <command>
```

Filter aggressively.

Keep:

```text
*.jl
*.m
*.py
*.sh
*.toml
*.json
*.yaml
*.yml
small *.txt input files
small config files
```

Exclude:

```text
system libraries
Julia compiled cache
MATLAB system toolboxes
Python site-packages, unless editable/local
large data files
large output files
temporary files
```

Do not enable full tracing by default for large parallel jobs.

---

## 7. Code Snapshot Strategy

Do not copy the entire project directory.

Use a logical snapshot plus content-addressed storage.

Recommended layout:

```text
~/.cslurm/
  jobs/
    123456/
      submit.json
      original.slurm
      instrumented.slurm
      file_manifest.json
      ai_summary.json
      completion_summary.json
      runtime/
        commands.log
        runtime_env.txt
      logs/
  store/
    sha256/
      ab/cdef...
      12/3456...
  db.sqlite
```

Each job stores a manifest. File contents are stored once by hash.

Example `file_manifest.json`:

```json
{
  "job_id": "123456",
  "files": [
    {
      "path": "/work/project/run_dmft.jl",
      "relpath": "run_dmft.jl",
      "sha256": "abc...",
      "size": 19342,
      "role": "entry_file",
      "source": "runtime-command",
      "copied": true
    },
    {
      "path": "/work/project/src/solver.jl",
      "relpath": "src/solver.jl",
      "sha256": "def...",
      "size": 22103,
      "role": "source_dependency",
      "source": "static-include",
      "copied": true
    },
    {
      "path": "/work/project/results/output.jld2",
      "size": 8352931840,
      "role": "large_output_candidate",
      "source": "detected-path",
      "copied": false
    }
  ]
}
```

Default copy policy:

```text
Always save:
  submit command
  original Slurm script
  instrumented Slurm script
  git commit
  git status
  git diff
  runtime command log
  environment snapshot
  Project.toml / Manifest.toml / requirements / environment.yml when relevant

Copy by content-addressed store:
  relevant source files smaller than threshold
  config/input files smaller than threshold
  local dev package source/diff

Do not copy by default:
  .git/
  result/
  output/
  data/
  cache/
  .julia/compiled/
  __pycache__/
  *.jld2
  *.mat
  *.h5
  *.npy
  *.npz
  *.png
  *.pdf
  *.mp4
  very large files
```

Large files should be represented by metadata:

```text
path
size
mtime
optional sha256
role
copied=false
```

---

## 8. Database Schema

Use SQLite initially. Add FTS5 for search.

### 8.1 jobs

```sql
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY,
  submitted_at TEXT,
  submit_cwd TEXT,
  effective_chdir TEXT,
  command TEXT,
  original_script_path TEXT,
  copied_script_path TEXT,
  job_name TEXT,
  partition TEXT,
  account TEXT,
  qos TEXT,
  array_spec TEXT,
  dependency TEXT,
  stdout_path TEXT,
  stderr_path TEXT,
  git_commit TEXT,
  git_dirty INTEGER,
  state TEXT,
  exit_code TEXT,
  reason TEXT,
  elapsed TEXT,
  max_rss TEXT,
  alloc_cpus INTEGER,
  nodelist TEXT,
  summary_json TEXT,
  completion_summary_json TEXT,
  tags TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

### 8.2 job_events

```sql
CREATE TABLE job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  event_time TEXT,
  event_type TEXT,
  command TEXT,
  cwd TEXT,
  note TEXT,
  raw_output TEXT
);
```

Recommended event types:

```text
SUBMITTED
STARTED
STEP_STARTED
COMMAND_EXECUTED
CANCEL_REQUESTED
SIGNAL_SENT
HELD
RELEASED
REQUEUED
TIME_LIMIT_CHANGED
DEPENDENCY_CHANGED
STATE_CHANGED
COMPLETED
FAILED
TIMEOUT
OUT_OF_MEMORY
NODE_FAIL
TRACKER_OBSERVATION
AI_SUMMARY_CREATED
AI_COMPLETION_SUMMARY_CREATED
```

### 8.3 job_commands

```sql
CREATE TABLE job_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  step_id TEXT,
  time TEXT,
  hostname TEXT,
  cwd TEXT,
  kind TEXT,
  executable TEXT,
  argv TEXT,
  entry_file TEXT,
  entry_file_abs TEXT,
  source TEXT
);
```

`source` examples:

```text
runtime-wrapper
shell-trace
strace-execve
manual
```

### 8.4 job_files

```sql
CREATE TABLE job_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  command_id INTEGER,
  path TEXT,
  relpath TEXT,
  sha256 TEXT,
  size INTEGER,
  role TEXT,
  source TEXT,
  copied INTEGER,
  confidence REAL
);
```

`source` examples:

```text
runtime-command
static-include
static-import
manifest-local-path
git-diff
strace-open
model-suggested
manual-note
```

### 8.5 Full-Text Search

```sql
CREATE VIRTUAL TABLE job_fts USING fts5(
  job_id,
  text
);
```

Index:

```text
submit command
AI summary
completion summary
tags
important parameters
file names
notes
failure reasons
```

---

## 9. AI Summarization

### 9.1 Submission Summary

AI input should be curated, not the full directory.

Provide:

```text
submit command
original Slurm script
runtime command log if already available
entry files
dependency manifest
git diff
git status
config files
Project.toml / Manifest.toml summaries
environment summary
```

AI output must be structured JSON.

Example schema:

```json
{
  "job_id": "123456",
  "project": "DMFT",
  "one_line_summary": "Run DMFT at U=4 using bad-Broyden mixing to test hybridization peak stability.",
  "scientific_goal": "...",
  "main_entry": "run_dmft.jl",
  "important_parameters": {
    "U": "4",
    "mixing": "broyden"
  },
  "important_files": [
    "run_dmft.jl",
    "src/solver.jl",
    "src/mixing.jl",
    "Project.toml",
    "Manifest.toml"
  ],
  "expected_outputs": [
    "stdout log",
    "spectral function output",
    "convergence history"
  ],
  "risk_notes": [
    "Sharp hybridization peaks may make mixing unstable."
  ],
  "tags": ["Julia", "DMFT", "Broyden", "hybridization"],
  "dependency_confidence": 0.82,
  "summary_confidence": 0.78
}
```

### 9.2 Completion Summary

When a job finishes, tracker should collect:

```text
final Slurm state
exit code
elapsed
MaxRSS
stdout/stderr tail
seff output if available
sacct details
OOM/TIMEOUT/NODE_FAIL indicators
important generated output paths if detectable
```

AI output:

```json
{
  "job_id": "123456",
  "completion_status": "FAILED",
  "failure_category": "OUT_OF_MEMORY",
  "human_summary": "The job was killed after memory usage exceeded the allocation.",
  "evidence": [
    "Slurm state: OUT_OF_MEMORY",
    "stderr contains: Killed"
  ],
  "likely_cause": "The matrix size or number of kept states exceeded available memory.",
  "recommended_next_steps": [
    "Reduce N or kept states.",
    "Request more memory.",
    "Add memory logging around matrix construction."
  ],
  "confidence": 0.86
}
```

Do not allow AI to overwrite factual Slurm fields. AI summaries are additional derived fields.

---

## 10. CLI Behavior

### 10.1 Submission

```bash
csbatch job.slurm
csbatch --trace-files job.slurm
csbatch --note "Testing DMFT peak stabilization with smaller mixing." job.slurm
```

Expected behavior:

```text
1. Record submit cwd and command.
2. Snapshot original script.
3. Generate instrumented script.
4. Submit via real sbatch --parsable.
5. Store job id.
6. Collect git/environment metadata.
7. Schedule or trigger summary generation.
```

### 10.2 Interactive Allocation

```bash
csalloc -p CPU -c 32 --mem=128G
```

Should record allocation metadata and install runtime command wrappers in the interactive shell if feasible.

### 10.3 Direct srun

```bash
csrun -p CPU -c 16 julia --project=. test.jl
```

If no existing allocation is detected, treat as standalone tracked execution.

If inside existing Slurm allocation, record as a step under the current job.

### 10.4 Cancellation and State Changes

```bash
cscancel 123456 --note "Wrong parameter U."
crequeue 123456 --note "Node failure; rerun."
chold 123456 --note "Wait for previous result."
crelease 123456
cupdate 123456 TimeLimit=08:00:00
```

Record the command, result, and note in `job_events`.

### 10.5 Query

```bash
cjobs recent
cjobs show 123456
cjobs logs 123456 --tail 200
cjobs files 123456
cjobs events 123456
cjobs ask "最近提交了什么任务？"
cjobs ask "哪些任务失败了，失败原因是什么？"
cjobs ask "最近有哪些 DMFT hybridization peak 相关任务？"
cjobs compare 123456 123890
```

---

## 11. Tracker Daemon

Implement as either:

```text
systemd --user timer
cron job
long-running daemon
```

Initial recommendation: systemd user timer or cron is simpler and robust.

Run every 5-10 minutes.

Tracker algorithm:

```text
1. Query DB for jobs in PENDING/RUNNING/UNKNOWN or recently completed jobs lacking completion summary.
2. Call squeue for active state.
3. Call sacct for accounting data.
4. Call scontrol show job for detailed state/reason/workdir.
5. Update jobs table.
6. Insert STATE_CHANGED events when state changes.
7. If job finished:
   - collect stdout/stderr tail
   - collect seff if available
   - detect OOM/TIMEOUT/FAILED/NODE_FAIL
   - run AI completion summarizer
   - write completion_summary_json
```

Avoid heavy polling. Batch `sacct` calls where possible.

---

## 12. Skill vs Agent vs MCP

### 12.1 External Agent / CLI

Required.

Responsible for:

```text
wrapping Slurm commands
recording facts
database writes
snapshotting files
polling Slurm
reading logs
calling AI summarizer
```

### 12.2 Skill

Optional.

Purpose:

```text
Teach Codex how to use cjobs.
Tell Codex never to answer from memory.
Tell Codex to query local DB through CLI/MCP.
```

A skill should not be the primary storage or tracking mechanism.

### 12.3 MCP Server

Recommended later.

Expose tools:

```text
list_recent_jobs(n)
search_jobs(query, since)
read_job(job_id)
read_job_log(job_id, tail_n)
read_job_files(job_id)
read_job_events(job_id)
compare_jobs(job_id_a, job_id_b)
get_failed_jobs(since)
```

This allows Codex/ChatGPT-like agents to query the tracking database without shell scraping.

---

## 13. Implementation Phases

### Phase 1: Deterministic Minimal System

Implement:

```text
csbatch
SQLite
jobs table
job_events table
record job_id, cwd, command, script, git commit, git diff
cjobs recent
cjobs show
basic tracker with sacct/squeue
```

No AI required yet.

### Phase 2: Runtime Command Log

Implement:

```text
instrumented Slurm script
runtime_env.txt
PATH wrappers for julia/python/matlab/srun
commands.log
job_commands table
```

Goal: answer what command actually ran.

### Phase 3: Static Dependency Analysis

Implement:

```text
Julia include parser
Project.toml / Manifest.toml collector
local dev package detector
Python import/config collector
MATLAB entry/addpath/local .m collector
file_manifest.json
job_files table
content-addressed store
```

Goal: answer what code and config files were relevant.

### Phase 4: AI Submission Summaries

Implement:

```text
curated prompt builder
structured JSON output
summary_json field
job_fts index
cjobs search
cjobs ask basic
```

Goal: semantic search and natural-language summaries.

### Phase 5: Completion Tracking and AI Diagnosis

Implement:

```text
completion detection
stdout/stderr tail ingestion
seff/sacct summary
completion_summary_json
failure category classification
```

Goal: answer what completed, failed, timed out, OOMed, or was cancelled.

### Phase 6: MCP and Optional Skill

Implement:

```text
local MCP server
Codex skill instructions
safe query interface
comparison tools
```

Goal: let coding agents use the job memory system directly.

---

## 14. Important Edge Cases

Handle:

```text
sbatch --wrap
sbatch --chdir
job arrays
dependencies: --dependency
multiple srun steps
interactive salloc sessions
direct srun without sbatch
jobs cancelled by user
jobs cancelled by system
requeue
hold/release
node failure
OOM
timeout
relative paths
symlinks
changed files after submission
untracked files
local dev packages
dynamic include/import/run
large files
missing sacct data
clusters without seff
clusters with custom modules
```

For job arrays, store parent job and array task id separately if possible.

---

## 15. Recommended Python Package Structure

```text
cslurm/
  __init__.py
  cli/
    csbatch.py
    csalloc.py
    csrun.py
    cscancel.py
    cjobs.py
  slurm/
    submit.py
    parse_sbatch.py
    query.py
    tracker.py
  runtime/
    generate_instrumented_script.py
    wrappers/
      julia
      python
      matlab
      srun
  collect/
    git.py
    env.py
    snapshot.py
    dependency_julia.py
    dependency_python.py
    dependency_matlab.py
    filetrace.py
  db/
    schema.sql
    models.py
    repository.py
  ai/
    prompt_submission.py
    prompt_completion.py
    schema_submission.json
    schema_completion.json
    summarize.py
  mcp/
    server.py
  config.py
```

Use a user config file:

```text
~/.cslurm/config.toml
```

Example:

```toml
[paths]
root = "~/.cslurm"
db = "~/.cslurm/db.sqlite"
store = "~/.cslurm/store"

[copy_policy]
max_source_file_mb = 2
max_config_file_mb = 10
hash_large_files = false

[commands]
sbatch = "/usr/bin/sbatch"
squeue = "/usr/bin/squeue"
sacct = "/usr/bin/sacct"
srun = "/usr/bin/srun"

[ai]
enabled = true
model = "..."
```

---

## 16. Minimal Acceptance Tests

A coding agent should implement tests for:

```text
1. csbatch records a job id from mocked sbatch --parsable output.
2. original Slurm script is copied.
3. instrumented script is generated.
4. git commit/diff are collected.
5. runtime julia wrapper writes commands.log.
6. Julia include parser finds local include files.
7. content-addressed store deduplicates identical files.
8. tracker updates job state from mocked sacct.
9. cscancel writes CANCEL_REQUESTED event.
10. cjobs show returns job metadata and summary.
11. AI summary parser rejects malformed JSON.
12. large files are not copied, only recorded.
```

---

## 17. Final Design Summary

The system should not ask AI to guess what ran.

Instead:

```text
runtime command log:
  records actual commands, cwd, executable, argv, host, job id.

static dependency analysis:
  determines likely code/config dependencies from entry files.

content-addressed snapshot:
  preserves relevant files without copying entire projects.

Slurm tracker:
  follows lifecycle, exit code, resources, and logs.

AI:
  summarizes purpose, important parameters, relation to previous jobs, and failure causes.
```

The recommended first implementation target is:

```text
csbatch + SQLite + git snapshot + runtime command log + basic tracker
```

Then add dependency analysis and AI summaries incrementally.
