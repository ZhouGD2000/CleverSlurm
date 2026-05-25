import re
import shlex
from collections.abc import Callable
from pathlib import Path


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")
LAUNCHER_NAMES = {"srun", "mpirun", "mpiexec", "env", "time", "timeout", "nice", "nohup", "stdbuf"}


def strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    kept = []
    for char in line:
        if escaped:
            kept.append(char)
            escaped = False
            continue
        if char == "\\":
            kept.append(char)
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        kept.append(char)
    return "".join(kept).strip()


def split_shell_commands(line: str) -> list[str]:
    commands = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == ";" and not in_single and not in_double:
            command = "".join(current).strip()
            if command:
                commands.append(command)
            current = []
            continue
        current.append(char)
    command = "".join(current).strip()
    if command:
        commands.append(command)
    return commands


def tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return []


def assignment_only(line: str) -> bool:
    match = ASSIGNMENT.match(line)
    if not match:
        return False
    tokens = tokenize(match.group(2))
    return len(tokens) <= 1


def read_assignments(script_text: str) -> dict[str, str]:
    assignments = {}
    for raw_line in script_text.splitlines():
        line = strip_inline_comment(raw_line)
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        match = ASSIGNMENT.match(line)
        if not match or not assignment_only(line):
            continue
        name, value = match.groups()
        tokens = tokenize(value)
        assignments[name] = tokens[0] if tokens else value.strip().strip("'\"")
    return assignments


def resolve_executable(token: str, assignments: dict[str, str]) -> str:
    if token.startswith("$"):
        return assignments.get(token[1:].strip("{}"), token)
    return token


def looks_like_env_assignment(token: str) -> bool:
    return bool(ASSIGNMENT.match(token))


def drop_leading_env_assignments(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens) and looks_like_env_assignment(tokens[index]):
        index += 1
    return tokens[index:]


def candidate_command(
    tokens: list[str],
    assignments: dict[str, str],
    classify_executable: Callable[[str], str | None],
) -> tuple[str, str, list[str]] | None:
    tokens = drop_leading_env_assignments(tokens)
    if not tokens:
        return None

    executable = resolve_executable(tokens[0], assignments)
    kind = classify_executable(executable)
    if kind:
        return kind, executable, tokens[1:]

    launcher = Path(executable).name.lower()
    if launcher not in LAUNCHER_NAMES:
        return None

    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("-") or looks_like_env_assignment(token):
            continue
        executable = resolve_executable(token, assignments)
        kind = classify_executable(executable)
        if kind:
            return kind, executable, tokens[index + 1 :]
    return None
