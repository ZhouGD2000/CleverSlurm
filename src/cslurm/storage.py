"""Keep SQLite off shared filesystems; compute nodes publish event files instead."""

import re
import sys
from pathlib import Path

from cslurm.config import db_path


class UnsafeDatabaseError(RuntimeError):
    pass


def filesystem_type(path: Path) -> str | None:
    if sys.platform != "linux":
        return None
    target = path.resolve()
    best = (-1, None)
    # mountinfo escapes spaces and backslashes as octal, including in mountpoints.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields, filesystem = line.split(" - ", 1)
        mount = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields.split()[4]))
        if target == mount or mount in target.parents:
            if len(mount.parts) > best[0]:
                best = (len(mount.parts), filesystem.split()[0])
    return best[1]


def require_local_database(path: Path | None = None) -> None:
    path = path or db_path()
    if (path.parent / ".database-maintenance").exists():
        raise UnsafeDatabaseError("Database maintenance is active; retry on the management host after recovery finishes.")
    fs = filesystem_type(path)
    if fs in {"nfs", "nfs4", "cifs", "smb3", "lustre", "ceph", "glusterfs", "gpfs", "9p"} or (fs and fs.startswith("fuse")):
        raise UnsafeDatabaseError(
            f"SQLite access refused on {fs}: {path}. Run database commands on the "
            "management host with local storage; use an SSH tunnel for cweb. "
            "csrun records are spooled for ingestion there."
        )
