import argparse
import os
import tempfile
from contextlib import closing

from cslurm.config import db_path
from cslurm.db import connect, init_db
from cslurm.web.data import check_integrity
from cslurm.web.server import ConsoleServer


def main():
    parser = argparse.ArgumentParser(prog="cweb", description="Loopback-only CleverSlurm web console. Use an SSH tunnel for remote access.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--read-only", action="store_true", help="Disable all web-initiated operations")
    parser.add_argument("--allow-cancel", action="store_true", help="Allow explicitly confirmed single-job cancellation")
    parser.add_argument("--demo", action="store_true", help="Use isolated temporary example data; never call Slurm or AI")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    temporary = tempfile.TemporaryDirectory(prefix="cslurm-web-demo-") if args.demo else None
    try:
        if temporary:
            os.environ["CSLURM_ROOT"] = temporary.name
        # Never migrate a damaged database, and never write in read-only mode.
        if not args.read_only and (not db_path().exists() or check_integrity() is None):
            with closing(connect()) as conn:
                init_db(conn)
        if args.demo:
            if args.read_only:
                with closing(connect()) as conn:
                    init_db(conn)
            from cslurm.web.demo import seed
            seed()
        with ConsoleServer(args.port, read_only=args.read_only, allow_cancel=args.allow_cancel, demo=args.demo) as server:
            port = server.server_address[1]
            print(f"CleverSlurm web console: http://127.0.0.1:{port}/#token={server.token}", flush=True)
            print("Keep this access link private. Press Ctrl+C to stop. Remote access: SSH port forwarding.", flush=True)
            if server.database_error:
                print(server.database_error, flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    main()
