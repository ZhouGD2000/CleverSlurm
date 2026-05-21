import argparse
from pathlib import Path

from cslurm import shims


def _add_bin_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bin-dir",
        type=Path,
        help="Directory where sbatch/srun/scancel shims are installed. Default: the directory containing csbatch.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="cshim")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install sbatch/srun/scancel shims.")
    _add_bin_dir(install_parser)
    install_parser.add_argument("--force", action="store_true", help="Overwrite unmanaged files in --bin-dir.")

    remove_parser = subparsers.add_parser("remove", aliases=["uninstall"], help="Remove managed shims.")
    _add_bin_dir(remove_parser)

    status_parser = subparsers.add_parser("status", help="Show shim status.")
    _add_bin_dir(status_parser)

    args = parser.parse_args()
    if args.command == "install":
        rows = shims.install(bin_dir=args.bin_dir, force=args.force)
        for row in rows:
            print(f"{row.slurm_command}\t{row.target}\t-> {row.clever_path}\treal={row.real_path}")
    elif args.command in {"remove", "uninstall"}:
        removed = shims.remove(bin_dir=args.bin_dir)
        if removed:
            for path in removed:
                print(f"removed\t{path}")
        else:
            print("no managed shims found")
    elif args.command == "status":
        for row in shims.status(bin_dir=args.bin_dir):
            state = "installed" if row.installed else "not-installed"
            active = row.active_path or "<not on PATH>"
            real = row.real_path or "<not found>"
            print(f"{row.slurm_command}\t{state}\ttarget={row.target}\tactive={active}\treal={real}")


if __name__ == "__main__":
    main()
