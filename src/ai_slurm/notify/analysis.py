import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HARD_FAILURE_STATES = {
    "FAILED",
    "OUT_OF_MEMORY",
    "TIMEOUT",
    "NODE_FAIL",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
    "SPECIAL_EXIT",
}

ABNORMAL_STATES = {"PREEMPTED", "REQUEUED"}

LOG_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE), category, confidence)
    for pattern, category, confidence in [
        (r"converged\s*=\s*false", "NOT_CONVERGED", 0.9),
        (r"not converged", "NOT_CONVERGED", 0.9),
        (r"max iteration reached", "NOT_CONVERGED", 0.88),
        (r"\bNaN\b", "NUMERICAL_INSTABILITY", 0.86),
        (r"\bInf\b", "NUMERICAL_INSTABILITY", 0.82),
        (r"diverged", "NUMERICAL_INSTABILITY", 0.86),
        (r"traceback \(most recent call last\)", "LOG_ERROR_PATTERN", 0.82),
        (r"segmentation fault", "LOG_ERROR_PATTERN", 0.9),
        (r"\bkilled\b", "LOG_ERROR_PATTERN", 0.82),
        (r"out\s+of\s+memory", "OUT_OF_MEMORY", 0.9),
        (r"outofmemory", "OUT_OF_MEMORY", 0.9),
        (r"\boom\b", "OUT_OF_MEMORY", 0.84),
        (r"exception", "LOG_ERROR_PATTERN", 0.75),
        (r"assertionerror", "LOG_ERROR_PATTERN", 0.8),
        (r"runtimeerror", "LOG_ERROR_PATTERN", 0.78),
        (r"moduleNotFoundError", "DEPENDENCY_OR_ENV_WARNING", 0.76),
        (r"importerror", "DEPENDENCY_OR_ENV_WARNING", 0.76),
        (r"loaderror", "LOG_ERROR_PATTERN", 0.78),
        (r"boundserror", "LOG_ERROR_PATTERN", 0.8),
        (r"dimensionmismatch", "LOG_ERROR_PATTERN", 0.8),
        (r"methoderror", "LOG_ERROR_PATTERN", 0.78),
        (r"error using", "LOG_ERROR_PATTERN", 0.78),
        (r"index exceeds", "LOG_ERROR_PATTERN", 0.78),
    ]
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(row: dict[str, Any], *names: str) -> Any:
    lower = {key.lower(): value for key, value in row.items()}
    for name in names:
        if name in row:
            return row[name]
        value = lower.get(name.lower())
        if value is not None:
            return value
    return None


def parse_slurm_exit_code(value: str | None) -> tuple[int, int]:
    if not value:
        return (0, 0)
    parts = value.strip().split(":")
    if len(parts) != 2:
        return (1, 0)
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return (1, 0)


def normalize_state(state: str | None) -> str:
    if not state:
        return "UNKNOWN"
    normalized = state.strip().upper()
    for sep in [" ", "+"]:
        if sep in normalized:
            normalized = normalized.split(sep, 1)[0]
    return normalized


def has_user_cancel_event(events: list[dict[str, Any]]) -> bool:
    return any(event.get("event_type") in {"CANCEL_REQUESTED", "USER_CANCELLED"} for event in events)


def classify_deterministic(row: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    state = normalize_state(_value(row, "State", "state"))
    exit_code, signal = parse_slurm_exit_code(_value(row, "ExitCode", "exit_code") or "0:0")
    derived_exit_code, derived_signal = parse_slurm_exit_code(
        _value(row, "DerivedExitCode", "derived_exit_code") or "0:0"
    )

    if state in HARD_FAILURE_STATES:
        return {
            "hard_failed": True,
            "deterministic_status": "hard_failed",
            "severity": "high",
            "failure_category": state,
            "recommended_notification": "immediate",
        }
    if exit_code != 0 or signal != 0:
        return {
            "hard_failed": True,
            "deterministic_status": "hard_failed",
            "severity": "high",
            "failure_category": "NONZERO_EXITCODE",
            "recommended_notification": "immediate",
        }
    if derived_exit_code != 0 or derived_signal != 0:
        return {
            "hard_failed": True,
            "deterministic_status": "hard_failed",
            "severity": "high",
            "failure_category": "NONZERO_DERIVED_EXITCODE",
            "recommended_notification": "immediate",
        }
    if state == "CANCELLED":
        if has_user_cancel_event(events):
            return {
                "hard_failed": False,
                "deterministic_status": "cancelled_by_user",
                "severity": "low",
                "failure_category": "USER_CANCELLED",
                "recommended_notification": "digest",
            }
        return {
            "hard_failed": False,
            "deterministic_status": "cancelled_unknown",
            "severity": "medium",
            "failure_category": "CANCELLED_UNKNOWN",
            "recommended_notification": "batch",
        }
    if state in ABNORMAL_STATES:
        return {
            "hard_failed": False,
            "deterministic_status": "abnormal",
            "severity": "medium",
            "failure_category": state,
            "recommended_notification": "batch",
        }
    if state == "COMPLETED":
        return {
            "hard_failed": False,
            "deterministic_status": "completed",
            "severity": "normal",
            "failure_category": "NONE",
            "recommended_notification": "batch",
        }
    return {
        "hard_failed": False,
        "deterministic_status": "unknown",
        "severity": "medium",
        "failure_category": "UNKNOWN",
        "recommended_notification": "batch",
    }


def _scan_file(path_text: str, *, context_lines: int = 3, max_matches: int = 20) -> list[dict[str, Any]]:
    path = Path(path_text).expanduser()
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []

    matches: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        for pattern, category, confidence in LOG_PATTERNS:
            if not pattern.search(line):
                continue
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            matches.append(
                {
                    "pattern": pattern.pattern,
                    "category": category,
                    "confidence": confidence,
                    "file": str(path),
                    "line_start": start + 1,
                    "line_end": end,
                    "lines": lines[start:end],
                }
            )
            break
        if len(matches) >= max_matches:
            break
    return matches


def scan_job_logs(row: dict[str, Any]) -> list[dict[str, Any]]:
    matches = []
    for key in ["stdout_path", "stderr_path"]:
        path = row.get(key)
        if path:
            matches.extend(_scan_file(path))
    return matches


def _parse_scalar(value: str) -> Any:
    normalized = value.strip().strip('"').strip("'")
    if normalized.lower() in {"true", "yes", "on"}:
        return True
    if normalized.lower() in {"false", "no", "off"}:
        return False
    return normalized


def parse_success_criteria(text: str) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    in_section = False
    current_list: str | None = None
    for raw_line in text.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        if not line_without_comment.strip():
            continue
        stripped = line_without_comment.strip()
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        if stripped == "success_criteria:":
            in_section = True
            current_list = None
            continue
        if not in_section and ":" not in stripped:
            continue
        if in_section and indent == 0 and stripped != "success_criteria:":
            break
        if stripped.startswith("- ") and current_list:
            criteria.setdefault(current_list, []).append(_parse_scalar(stripped[2:]))
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not in_section and key not in {
                "require_exit_zero",
                "require_output_files",
                "log_must_contain",
                "log_must_not_contain",
            }:
                continue
            if value:
                criteria[key] = _parse_scalar(value)
                current_list = None
            else:
                criteria[key] = []
                current_list = key
    return criteria


def _criteria_path(row: dict[str, Any]) -> Path | None:
    env_path = os.environ.get("AI_SLURM_SUCCESS_CRITERIA")
    if env_path:
        return Path(env_path).expanduser()
    submit_cwd = row.get("effective_chdir") or row.get("submit_cwd")
    if submit_cwd:
        candidate = Path(submit_cwd).expanduser() / ".ai-slurm.yml"
        if candidate.exists():
            return candidate
    return None


def load_success_criteria(row: dict[str, Any]) -> dict[str, Any]:
    path = _criteria_path(row)
    if not path:
        return {}
    try:
        return parse_success_criteria(path.read_text())
    except OSError:
        return {}


def _combined_logs(row: dict[str, Any]) -> str:
    chunks = []
    for key in ["stdout_path", "stderr_path"]:
        path_text = row.get(key)
        if not path_text:
            continue
        try:
            chunks.append(Path(path_text).expanduser().read_text(errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def check_success_criteria(row: dict[str, Any], criteria: dict[str, Any]) -> list[dict[str, Any]]:
    if not criteria:
        return []
    failures: list[dict[str, Any]] = []
    base_dir = Path(row.get("effective_chdir") or row.get("submit_cwd") or ".").expanduser()
    for pattern in criteria.get("require_output_files") or []:
        matches = list(base_dir.glob(str(pattern)))
        if not matches:
            failures.append({"check": "require_output_files", "pattern": str(pattern), "reason": "missing"})

    logs = _combined_logs(row)
    for needle in criteria.get("log_must_contain") or []:
        if str(needle) not in logs:
            failures.append({"check": "log_must_contain", "pattern": str(needle), "reason": "missing"})
    for needle in criteria.get("log_must_not_contain") or []:
        if str(needle) in logs:
            failures.append({"check": "log_must_not_contain", "pattern": str(needle), "reason": "present"})
    return failures


def decide_notification(analysis: dict[str, Any]) -> str:
    if analysis.get("severity") == "high":
        return "immediate"
    if analysis.get("deterministic_status") == "hard_failed":
        return "immediate"
    if analysis.get("failure_category") in {"OUT_OF_MEMORY", "TIMEOUT", "NODE_FAIL"}:
        return "immediate"
    if analysis.get("semantic_status") == "semantic_failed":
        return "immediate" if analysis.get("confidence", 0.0) >= 0.85 else "batch"
    if analysis.get("semantic_status") in {"suspicious", "success_with_warning", "normal"}:
        return "batch"
    if analysis.get("deterministic_status") == "cancelled_by_user":
        return "digest"
    return analysis.get("recommended_notification") or "batch"


def _semantic_from_matches(matches: list[dict[str, Any]]) -> tuple[str, str, float, str]:
    if not matches:
        return ("normal", "NONE", 1.0, "normal")
    strongest = max(matches, key=lambda match: float(match.get("confidence") or 0.0))
    category = str(strongest.get("category") or "LOG_ERROR_PATTERN")
    confidence = float(strongest.get("confidence") or 0.0)
    if category in {"NOT_CONVERGED", "NUMERICAL_INSTABILITY", "OUT_OF_MEMORY"} or confidence >= 0.85:
        return ("semantic_failed", category, confidence, "medium")
    return ("suspicious", category, confidence, "medium")


def build_notification_text(analysis: dict[str, Any]) -> tuple[str, str]:
    job_id = analysis["job_id"]
    category = analysis["failure_category"]
    state = analysis["slurm_state"]
    title = f"[AI-Slurm][{category}] Job {job_id}"
    lines = [
        f"State: {state}",
        f"ExitCode: {analysis.get('exit_code') or ''}",
        f"DerivedExitCode: {analysis.get('derived_exit_code') or ''}",
        f"Status: {analysis.get('deterministic_status')}",
        f"Semantic: {analysis.get('semantic_status')}",
        f"Severity: {analysis.get('severity')}",
    ]
    matched = analysis.get("evidence", {}).get("matched_windows", [])
    if matched:
        lines.append("")
        lines.append("Evidence:")
        for item in matched[:3]:
            lines.append(f"- {item.get('file')}:{item.get('line_start')} matched {item.get('category')}")
    return title, "\n".join(lines)


def analyze_job(conn, job_id: str) -> dict[str, Any]:
    job = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    row = {key: job[key] for key in job.keys()}
    event_rows = conn.execute(
        "select id, event_time, event_type, command, note, raw_output from job_events where job_id = ? order by id",
        (job_id,),
    ).fetchall()
    events = [{key: event[key] for key in event.keys()} for event in event_rows]
    state = normalize_state(row.get("state"))
    analysis = classify_deterministic(row, events)
    matches = scan_job_logs(row)
    criteria_failures = check_success_criteria(row, load_success_criteria(row))
    semantic_status, semantic_category, confidence, semantic_severity = _semantic_from_matches(matches)

    if analysis["hard_failed"]:
        semantic_status = "hard_failed"
        confidence = 1.0
    elif criteria_failures:
        semantic_status = "semantic_failed"
        semantic_category = "SUCCESS_CRITERIA_NOT_MET"
        confidence = 0.9
        semantic_severity = "medium"
        analysis["failure_category"] = semantic_category
        analysis["severity"] = semantic_severity
    elif semantic_status != "normal":
        analysis["failure_category"] = semantic_category
        analysis["severity"] = semantic_severity
    elif analysis["deterministic_status"] != "completed":
        semantic_status = "unknown"
        confidence = 0.0

    analysis.update(
        {
            "job_id": job_id,
            "slurm_state": state,
            "exit_code": row.get("exit_code"),
            "derived_exit_code": row.get("derived_exit_code"),
            "semantic_status": semantic_status,
            "confidence": confidence,
            "evidence": {"matched_windows": matches, "success_criteria_failures": criteria_failures},
        }
    )
    analysis["recommended_notification"] = decide_notification(analysis)
    title, body = build_notification_text(analysis)
    analysis["title"] = title
    analysis["body"] = body
    return analysis


def record_job_analysis(conn, analysis: dict[str, Any], ai_analysis: dict[str, Any] | None = None) -> int:
    cursor = conn.execute(
        """
        insert into job_analysis (
          job_id, created_at, slurm_state, exit_code, derived_exit_code, hard_failed,
          deterministic_status, semantic_status, failure_category, severity, confidence,
          evidence_json, ai_analysis_json, recommended_notification
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            analysis["job_id"],
            _now(),
            analysis.get("slurm_state"),
            analysis.get("exit_code"),
            analysis.get("derived_exit_code"),
            1 if analysis.get("hard_failed") else 0,
            analysis.get("deterministic_status"),
            analysis.get("semantic_status"),
            analysis.get("failure_category"),
            analysis.get("severity"),
            analysis.get("confidence"),
            json.dumps(analysis.get("evidence") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(ai_analysis, ensure_ascii=False, sort_keys=True) if ai_analysis else None,
            analysis.get("recommended_notification"),
        ),
    )
    return int(cursor.lastrowid)
