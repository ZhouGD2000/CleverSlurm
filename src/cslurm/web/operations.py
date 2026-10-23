import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from cslurm.config import root_dir
from cslurm.web.data import ACTIVE_STATES, get_job


class BusyError(Exception):
    pass


class Operations:
    """One bounded background operation at a time; no shell or arbitrary commands."""

    def __init__(self, *, read_only=False, allow_cancel=False, demo=False):
        self.read_only = read_only
        self.allow_cancel = allow_cancel
        self.demo = demo
        self.lock = threading.Lock()
        self.items = {}
        self.active = None
        self.worker = None
        self.process = None
        self.stopping = False

    def get(self, operation_id):
        with self.lock:
            if operation_id not in self.items:
                raise KeyError("Operation expired or server restarted")
            return dict(self.items[operation_id])

    def start(self, action, body):
        if self.read_only:
            raise PermissionError("Read-only mode: operations are disabled")
        if self.demo:
            raise PermissionError("Demo data: live operations are disabled")
        if action not in {"refresh", "summarize", "ask", "cancel"}:
            raise ValueError("Unknown operation")
        job_id = body.get("job_id")
        if action in {"summarize", "cancel"}:
            if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", job_id) or job_id.startswith("-"):
                raise ValueError("Invalid job ID")
            job = get_job(job_id)
        if action == "cancel":
            if not self.allow_cancel:
                raise PermissionError("Cancellation is disabled; start cweb with --allow-cancel to enable it")
            if not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", job_id) or (job.get("array_spec") and "_" not in job_id):
                raise ValueError("Only one numeric job or array task may be cancelled; array parents are not supported")
            if body.get("confirm_job_id") != job_id:
                raise ValueError("Type the exact job ID to confirm cancellation")
            if job["state"] not in ACTIVE_STATES:
                raise ValueError("Only a known active job can be cancelled; refresh its state first")
            args = ["cslurm.cli.cscancel", job_id, "--note", "Confirmed single-job cancellation from cweb"]
        elif action == "refresh":
            args = ["cslurm.cli.ctrack"]
        elif action == "summarize":
            kind = body.get("kind", "submission")
            if kind not in {"submission", "completion"}:
                raise ValueError("Invalid summary kind")
            args = ["cslurm.cli.csummarize", job_id] + (["--completion"] if kind == "completion" else [])
        else:
            question = body.get("question")
            if not isinstance(question, str) or not question.strip() or len(question) > 2000:
                raise ValueError("Question must contain 1 to 2000 characters")
            args = ["cslurm.cli.cjobs", "ask", "--", question.strip()]
        with self.lock:
            if self.stopping:
                raise BusyError("The web server is stopping")
            if self.active:
                raise BusyError("An operation is already running; wait for it to finish")
            while len(self.items) >= 50:
                del self.items[next(iter(self.items))]
            operation_id = uuid.uuid4().hex
            self.items[operation_id] = {
                "id": operation_id, "action": action, "job_id": job_id, "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(), "result": None, "error": None,
            }
            self.active = operation_id
            self.worker = threading.Thread(target=self._run, args=(operation_id, args), daemon=True)
            self.worker.start()
            return dict(self.items[operation_id])

    @staticmethod
    def _terminate(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def close(self):
        with self.lock:
            self.stopping = True
            process, worker = self.process, self.worker
        if process:
            self._terminate(process)
        if worker:
            worker.join(timeout=8)

    def _run(self, operation_id, args):
        lock_file = None
        result, error = None, None
        try:
            if args[0] == "cslurm.cli.ctrack":
                import fcntl
                lock_file = (root_dir() / "ctrack.lock").open("a")
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BusyError("Automatic tracking is already running; try again later") from None
            with tempfile.TemporaryFile() as output:
                with self.lock:
                    if self.stopping:
                        raise BusyError("The web server is stopping")
                    process = subprocess.Popen(
                        [sys.executable, "-m", *args], stdout=output, stderr=output,
                        stdin=subprocess.DEVNULL, start_new_session=True,
                    )
                    self.process = process
                try:
                    code = process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    self._terminate(process)
                    raise TimeoutError("Operation exceeded 300 seconds; check job events before retrying") from None
                if code:
                    # Provider errors may contain credentials or webhook URLs. Never expose tracebacks.
                    raise RuntimeError(f"Command exited with status {code}. Inspect the job events on the server.")
                output.seek(0)
                result = output.read(64 * 1024).decode("utf-8", errors="replace").strip() or "Done"
        except Exception as exc:
            error = str(exc) if isinstance(exc, (BusyError, TimeoutError, RuntimeError)) else "Operation failed on the server"
        finally:
            if lock_file:
                lock_file.close()
            with self.lock:
                self.items[operation_id].update(status="failed" if error else "succeeded", result=result, error=error)
                self.active = None
                self.process = None
