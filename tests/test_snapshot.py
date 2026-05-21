import sqlite3

from cslurm.collect.snapshot import snapshot_files
from cslurm.db import connect, init_db


def test_content_addressed_store_deduplicates_identical_files(isolated_home, tmp_path):
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("print(1)\n")
    second.write_text("print(1)\n")
    with connect() as conn:
        init_db(conn)
        snapshot_files(conn, "123456", [first, second])

    stored = [p for p in (isolated_home / "store" / "sha256").rglob("*") if p.is_file()]
    assert len(stored) == 1

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        rows = conn.execute("select path, sha256, copied from job_files order by path").fetchall()
    assert len(rows) == 2
    assert rows[0][1] == rows[1][1]
    assert rows[0][2] == rows[1][2] == 1


def test_large_files_are_recorded_but_not_copied(isolated_home, tmp_path):
    large = tmp_path / "large.dat"
    large.write_bytes(b"x" * 20)
    with connect() as conn:
        init_db(conn)
        manifest = snapshot_files(conn, "123456", [large], max_copy_bytes=10)

    assert manifest["files"][0]["copied"] is False
    assert manifest["files"][0]["size"] == 20
    stored = [p for p in (isolated_home / "store" / "sha256").rglob("*") if p.is_file()]
    assert stored == []
