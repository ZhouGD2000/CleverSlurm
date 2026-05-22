from pathlib import Path

from cslurm.collect.static_script import find_static_commands


def test_static_script_finds_matlab_entry_from_variable_command(tmp_path):
    (tmp_path / "Dinf.m").write_text("disp('run')\n")
    script_text = (
        "#!/bin/bash\n"
        "EXE=/home/software/MATLAB/R2022b/bin/matlab\n"
        "$EXE -nodisplay -r Dinf;exit;\n"
    )

    commands = find_static_commands(script_text, tmp_path)

    assert len(commands) == 1
    assert commands[0].kind == "matlab"
    assert commands[0].executable == "/home/software/MATLAB/R2022b/bin/matlab"
    assert commands[0].argv == ["-nodisplay", "-r", "Dinf"]
    assert commands[0].entry_file == "Dinf.m"
    assert commands[0].entry_file_abs == str(tmp_path / "Dinf.m")


def test_static_script_finds_python_entry_from_variable_command(tmp_path):
    (tmp_path / "run.py").write_text("print('run')\n")
    script_text = "#!/bin/bash\nPY=/usr/bin/python3\n$PY run.py --alpha 1\n"

    commands = find_static_commands(script_text, tmp_path)

    assert len(commands) == 1
    assert commands[0].kind == "python"
    assert commands[0].executable == "/usr/bin/python3"
    assert commands[0].argv == ["run.py", "--alpha", "1"]
    assert commands[0].entry_file == "run.py"
    assert commands[0].entry_file_abs == str(tmp_path / "run.py")


def test_static_script_finds_python_under_srun_with_env_assignment(tmp_path):
    (tmp_path / "train.py").write_text("print('train')\n")
    script_text = "OMP_NUM_THREADS=4 srun --gres=gpu:1 python3 train.py --epochs 2\n"

    commands = find_static_commands(script_text, tmp_path)

    assert len(commands) == 1
    assert commands[0].kind == "python"
    assert commands[0].executable == "python3"
    assert commands[0].argv == ["train.py", "--epochs", "2"]
    assert commands[0].entry_file_abs == str(tmp_path / "train.py")


def test_static_script_leaves_missing_entry_abs_empty(tmp_path):
    script_text = "python3 missing.py\n"

    commands = find_static_commands(script_text, Path(tmp_path))

    assert len(commands) == 1
    assert commands[0].entry_file == "missing.py"
    assert commands[0].entry_file_abs is None
