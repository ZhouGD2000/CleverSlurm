import json


SUBMISSION_SYSTEM_PROMPT = """\
You summarize Slurm job submissions. Facts are deterministic records; do not invent missing facts.
Return one JSON object with keys such as job_id, one_line_summary, scientific_goal,
important_parameters, important_files, expected_outputs, risk_notes, tags,
dependency_confidence, and summary_confidence.
"""


COMPLETION_SYSTEM_PROMPT = """\
You summarize Slurm job completion status. Do not overwrite factual Slurm fields.
Return one JSON object with keys such as job_id, completion_status, failure_category,
human_summary, evidence, likely_cause, recommended_next_steps, and confidence.
"""


def submission_messages(facts: dict) -> list[dict]:
    return [
        {"role": "system", "content": SUBMISSION_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)},
    ]


def completion_messages(facts: dict) -> list[dict]:
    return [
        {"role": "system", "content": COMPLETION_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False, indent=2)},
    ]
