from ai_slurm.db import connect, init_db
from ai_slurm.notify.dispatcher import pending_notifications
from ai_slurm.notify.feishu import dispatch_pending


def _format_pending(limit: int) -> str:
    with connect() as conn:
        init_db(conn)
        rows = pending_notifications(conn, limit=limit)
    return "\n".join(
        "\t".join(
            [
                str(row["id"]),
                row["job_id"] or "",
                row["mode"] or "",
                row["severity"] or "",
                row["category"] or "",
                row["title"] or "",
            ]
        )
        for row in rows
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="ainotify")
    subparsers = parser.add_subparsers(dest="command", required=True)
    pending = subparsers.add_parser("pending")
    pending.add_argument("-n", "--limit", type=int, default=50)
    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("-n", "--limit", type=int, default=50)
    args = parser.parse_args()

    if args.command == "pending":
        print(_format_pending(args.limit))
    elif args.command == "dispatch":
        sent = dispatch_pending(limit=args.limit)
        print(f"sent {sent} notification(s)")


if __name__ == "__main__":
    main()
