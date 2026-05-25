from pathlib import Path

from cslurm.collect.static.common import StaticCommand
import cslurm.collect.static.matlab as matlab
import cslurm.collect.static.python as python
from cslurm.collect.static.shell import (
    assignment_only,
    candidate_command,
    read_assignments,
    split_shell_commands,
    strip_inline_comment,
    tokenize,
)


def command_kind(executable: str) -> str | None:
    if matlab.is_matlab_executable(executable):
        return "matlab"
    if python.is_python_executable(executable):
        return "python"
    return None


def _entry_for(kind: str, argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    if kind == "python":
        return python.entry_from_argv(argv, script_dir)
    if kind == "matlab":
        return matlab.entry_from_argv(argv, script_dir)
    return None, None


def find_static_commands(script_text: str, script_dir: Path) -> list[StaticCommand]:
    assignments = read_assignments(script_text)
    commands = []
    for raw_line in script_text.splitlines():
        line = strip_inline_comment(raw_line)
        if not line or line.startswith("#") or assignment_only(line):
            continue
        for shell_command in split_shell_commands(line):
            tokens = tokenize(shell_command)
            if not tokens:
                continue
            command = candidate_command(tokens, assignments, command_kind)
            if not command:
                continue
            kind, executable, argv = command
            entry_file, entry_file_abs = _entry_for(kind, argv, script_dir)
            commands.append(
                StaticCommand(
                    kind=kind,
                    executable=executable,
                    argv=argv,
                    entry_file=entry_file,
                    entry_file_abs=entry_file_abs,
                )
            )
    return commands
