import argparse
from pathlib import Path

from cslurm import auto_track
from cslurm.slurm.tracker import track_once


def main() -> None:
    parser = argparse.ArgumentParser(prog="ctrack")
    subparsers = parser.add_subparsers(dest="command")
    auto = subparsers.add_parser("auto", help="Manage cron-based automatic tracking.")
    auto_subparsers = auto.add_subparsers(dest="auto_command", required=True)
    for name in ["on", "enable", "start", "restart"]:
        subparser = auto_subparsers.add_parser(name)
        subparser.add_argument("--repo", type=Path, help="Repository directory to cd into before running ctrack.")
        subparser.add_argument("--python", dest="python_executable", help="Python executable for the cron command.")
        subparser.add_argument("--schedule", default="* * * * *", help="Five-field cron schedule. Default: every minute.")
    auto_subparsers.add_parser("off")
    auto_subparsers.add_parser("disable")
    auto_subparsers.add_parser("stop")
    auto_subparsers.add_parser("status")
    args = parser.parse_args()

    if args.command != "auto":
        track_once()
        return

    if args.auto_command in {"on", "enable", "start"}:
        line = auto_track.enable(repo_dir=args.repo, python_executable=args.python_executable, schedule=args.schedule)
        print("auto tracking enabled")
        print(line)
        return
    if args.auto_command == "restart":
        line = auto_track.restart(repo_dir=args.repo, python_executable=args.python_executable, schedule=args.schedule)
        print("auto tracking restarted")
        print(line)
        return
    if args.auto_command in {"off", "disable", "stop"}:
        was_enabled = auto_track.disable()
        print("auto tracking disabled" if was_enabled else "auto tracking was not enabled")
        return
    if args.auto_command == "status":
        status = auto_track.status()
        print("enabled" if status.enabled else "disabled")
        if status.line:
            print(status.line)


if __name__ == "__main__":
    main()
