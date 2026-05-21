from pathlib import Path


def test_auto_track_enable_replaces_only_managed_cron_block(isolated_home, monkeypatch, tmp_path):
    from cslurm import auto_track

    repo = tmp_path / "repo"
    (repo / "src" / "cslurm").mkdir(parents=True)
    written = []
    current = "\n".join(
        [
            "15 3 * * * echo keep",
            auto_track.BEGIN_MARKER,
            "* * * * * old command",
            auto_track.END_MARKER,
            "30 4 * * * echo also-keep",
            "",
        ]
    )

    monkeypatch.setattr(auto_track, "_read_crontab", lambda: current)
    monkeypatch.setattr(auto_track, "_write_crontab", lambda text: written.append(text))

    line = auto_track.enable(repo_dir=repo, python_executable="/usr/bin/python3")

    assert "cd " + str(repo) in line
    assert "PYTHONPATH=" + str(repo / "src") in line
    assert "CSLURM_ROOT=" + str(isolated_home) in line
    assert "old command" not in written[0]
    assert "15 3 * * * echo keep" in written[0]
    assert "30 4 * * * echo also-keep" in written[0]
    assert written[0].count(auto_track.BEGIN_MARKER) == 1


def test_auto_track_disable_removes_managed_block(monkeypatch):
    from cslurm import auto_track

    written = []
    current = "\n".join(
        [
            "15 3 * * * echo keep",
            auto_track.BEGIN_MARKER,
            "* * * * * ctrack command",
            auto_track.END_MARKER,
        ]
    )

    monkeypatch.setattr(auto_track, "_read_crontab", lambda: current)
    monkeypatch.setattr(auto_track, "_write_crontab", lambda text: written.append(text))

    assert auto_track.disable() is True
    assert written == ["15 3 * * * echo keep\n"]


def test_auto_track_disable_does_not_rewrite_unmanaged_crontab(monkeypatch):
    from cslurm import auto_track

    written = []

    monkeypatch.setattr(auto_track, "_read_crontab", lambda: "15 3 * * * echo keep\n")
    monkeypatch.setattr(auto_track, "_write_crontab", lambda text: written.append(text))

    assert auto_track.disable() is False
    assert written == []


def test_auto_track_status_reports_managed_line(monkeypatch):
    from cslurm import auto_track

    line = "* * * * * cd /repo && ctrack"
    monkeypatch.setattr(
        auto_track,
        "_read_crontab",
        lambda: "\n".join([auto_track.BEGIN_MARKER, line, auto_track.END_MARKER]),
    )

    status = auto_track.status()

    assert status.enabled is True
    assert status.line == line


def test_ctrack_auto_on_cli(monkeypatch, capsys, tmp_path):
    from cslurm.cli.ctrack import main

    calls = []

    def fake_enable(*, repo_dir: Path | None, python_executable: str | None, schedule: str):
        calls.append((repo_dir, python_executable, schedule))
        return "* * * * * fake ctrack"

    monkeypatch.setattr("cslurm.cli.ctrack.auto_track.enable", fake_enable)
    monkeypatch.setattr(
        "sys.argv",
        ["ctrack", "auto", "on", "--repo", str(tmp_path), "--python", "/usr/bin/python3", "--schedule", "*/5 * * * *"],
    )

    main()

    assert calls == [(tmp_path, "/usr/bin/python3", "*/5 * * * *")]
    assert capsys.readouterr().out == "auto tracking enabled\n* * * * * fake ctrack\n"


def test_ctrack_auto_status_cli(monkeypatch, capsys):
    from cslurm.auto_track import AutoTrackStatus
    from cslurm.cli.ctrack import main

    monkeypatch.setattr(
        "cslurm.cli.ctrack.auto_track.status",
        lambda: AutoTrackStatus(enabled=True, line="* * * * * fake ctrack"),
    )
    monkeypatch.setattr("sys.argv", ["ctrack", "auto", "status"])

    main()

    assert capsys.readouterr().out == "enabled\n* * * * * fake ctrack\n"
