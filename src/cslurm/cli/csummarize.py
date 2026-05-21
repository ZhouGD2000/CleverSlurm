import argparse
import json

from cslurm.ai.summarize import summarize_completion, summarize_submission


def main() -> None:
    parser = argparse.ArgumentParser(prog="csummarize")
    parser.add_argument("job_id")
    parser.add_argument(
        "--completion",
        action="store_true",
        help="Create a completion/failure summary instead of a submission summary.",
    )
    parser.add_argument("--model", help="Override the configured AI model for this request.")
    parser.add_argument("--max-tokens", type=int, help="Override max output tokens for this request.")
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Send enable_thinking=true for models that support it.",
    )
    args = parser.parse_args()

    if args.completion:
        summary = summarize_completion(
            args.job_id,
            model=args.model,
            max_tokens=args.max_tokens,
            enable_thinking=True if args.enable_thinking else None,
        )
    else:
        summary = summarize_submission(
            args.job_id,
            model=args.model,
            max_tokens=args.max_tokens,
            enable_thinking=True if args.enable_thinking else None,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
