# Fake Slurm MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a minimal deterministic AI-Slurm core that can be tested locally with fake Slurm commands.

**Architecture:** Use a small Python package with an injectable command runner/config layer. CLI entrypoints call fake or real Slurm commands through configured paths, persist facts in SQLite, and expose basic query commands.

**Tech Stack:** Python standard library, SQLite, pytest.

---

### Task 1: Test aisbatch with fake sbatch

**Files:**
- Create: `tests/test_aisbatch.py`
- Create: `tests/conftest.py`
- Create: `ai_slurm/cli/aisbatch.py`

**Steps:**
1. Write a pytest that creates a fake `sbatch` returning `123456`, submits a temporary `.slurm` file, and asserts the DB row and copied scripts exist.
2. Run the test and verify it fails because `ai_slurm` does not exist.
3. Implement minimal config, DB schema, Slurm command runner, and `submit_batch`.
4. Run the test and verify it passes.

### Task 2: Test tracker with fake sacct

**Files:**
- Create: `tests/test_tracker.py`
- Create: `ai_slurm/slurm/tracker.py`

**Steps:**
1. Write a pytest that seeds a job, points `sacct` at a fake command, runs tracker once, and asserts state/exit code update plus `STATE_CHANGED` event.
2. Run the test and verify it fails.
3. Implement minimal `track_once`.
4. Run the test and verify it passes.

### Task 3: Test aiscancel with fake scancel

**Files:**
- Create: `tests/test_aiscancel.py`
- Create: `ai_slurm/cli/aiscancel.py`

**Steps:**
1. Write a pytest that invokes cancel with a note and asserts a `CANCEL_REQUESTED` event.
2. Run the test and verify it fails.
3. Implement minimal cancel wrapper.
4. Run the test and verify it passes.

### Task 4: Test aijobs show

**Files:**
- Create: `tests/test_aijobs.py`
- Create: `ai_slurm/cli/aijobs.py`

**Steps:**
1. Write a pytest that inserts job metadata and asserts `show_job` returns readable text.
2. Run the test and verify it fails.
3. Implement minimal show/recent helpers.
4. Run the full test suite.
