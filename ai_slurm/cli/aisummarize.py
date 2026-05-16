import argparse
import json

from ai_slurm.ai.summarize import summarize_completion, summarize_submission


def main() -> None:
    parser = argparse.ArgumentParser(prog="aisummarize")
    parser.add_argument("job_id")
    parser.add_argument(
        "--completion",
        action="store_true",
        help="Create a completion/failure summary instead of a submission summary.",
    )
    args = parser.parse_args()

    if args.completion:
        summary = summarize_completion(args.job_id)
    else:
        summary = summarize_submission(args.job_id)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
