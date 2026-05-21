import hashlib
import shutil
from pathlib import Path

from cslurm.config import root_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_path(sha256: str) -> Path:
    return root_dir() / "store" / "sha256" / sha256[:2] / sha256[2:]


def snapshot_files(
    conn,
    job_id: str,
    paths: list[Path],
    *,
    max_copy_bytes: int = 2 * 1024 * 1024,
    role: str = "source_dependency",
    source: str = "manual",
) -> dict:
    files = []
    for raw_path in paths:
        path = raw_path.resolve()
        size = path.stat().st_size
        copied = size <= max_copy_bytes
        sha = _sha256(path) if copied else None

        if copied and sha is not None:
            target = _store_path(sha)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(path, target)

        conn.execute(
            """
            insert into job_files (job_id, path, relpath, sha256, size, role, source, copied, confidence)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                str(path),
                path.name,
                sha,
                size,
                role,
                source,
                1 if copied else 0,
                1.0,
            ),
        )
        files.append(
            {
                "path": str(path),
                "relpath": path.name,
                "sha256": sha,
                "size": size,
                "role": role,
                "source": source,
                "copied": copied,
            }
        )
    conn.commit()
    return {"job_id": job_id, "files": files}
