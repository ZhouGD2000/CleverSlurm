import subprocess
from pathlib import Path


def test_tracked_files_do_not_contain_personal_paths():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    tracked = [name for name in result.stdout.decode().split("\0") if name]
    forbidden = [
        "/" + "home" + "/" + "z" + "gd",
        "/" + "Users" + "/" + "z" + "gd",
        "Nut" + "store Files",
    ]
    offenders = []
    for name in tracked:
        path = repo / name
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        for pattern in forbidden:
            if pattern in text:
                offenders.append(f"{name}: {pattern}")
    assert offenders == []
