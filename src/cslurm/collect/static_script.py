import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")
PYTHON_NAMES = {"python", "python3", "python2", "python.exe"}
MATLAB_NAMES = {"matlab", "matlab.exe"}


@dataclass(frozen=True)
class StaticCommand:
    kind: str
    executable: str
    argv: list[str]
    entry_file: str | None
    entry_file_abs: str | None


@dataclass(frozen=True)
class StaticInsertResult:
    commands: int
    files: int


def _strip_inline_comment(line: str) -> str:
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


def _split_shell_commands(line: str) -> list[str]:
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


def _tokenize(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return []


def _assignment_only(line: str) -> bool:
    match = ASSIGNMENT.match(line)
    if not match:
        return False
    tokens = _tokenize(match.group(2))
    return len(tokens) <= 1


def _read_assignments(script_text: str) -> dict[str, str]:
    assignments = {}
    for raw_line in script_text.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        match = ASSIGNMENT.match(line)
        if not match or not _assignment_only(line):
            continue
        name, value = match.groups()
        tokens = _tokenize(value)
        assignments[name] = tokens[0] if tokens else value.strip().strip("'\"")
    return assignments


def _resolve_executable(token: str, assignments: dict[str, str]) -> str:
    if token.startswith("$"):
        return assignments.get(token[1:].strip("{}"), token)
    return token


def _command_kind(executable: str) -> str | None:
    name = Path(executable).name.lower()
    if name in MATLAB_NAMES:
        return "matlab"
    if name in PYTHON_NAMES or re.fullmatch(r"python\d+(\.\d+)?", name):
        return "python"
    return None


def _looks_like_env_assignment(token: str) -> bool:
    return bool(ASSIGNMENT.match(token))


def _drop_leading_env_assignments(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens) and _looks_like_env_assignment(tokens[index]):
        index += 1
    return tokens[index:]


def _candidate_command(tokens: list[str], assignments: dict[str, str]) -> tuple[str, str, list[str]] | None:
    tokens = _drop_leading_env_assignments(tokens)
    if not tokens:
        return None

    executable = _resolve_executable(tokens[0], assignments)
    kind = _command_kind(executable)
    if kind:
        return kind, executable, tokens[1:]

    launcher = Path(executable).name.lower()
    if launcher not in {"srun", "mpirun", "mpiexec", "env", "time", "timeout", "nice", "nohup", "stdbuf"}:
        return None

    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("-") or _looks_like_env_assignment(token):
            continue
        executable = _resolve_executable(token, assignments)
        kind = _command_kind(executable)
        if kind:
            return kind, executable, tokens[index + 1 :]
    return None


def _resolve_entry(path_text: str, script_dir: Path, suffix: str) -> tuple[str, str | None]:
    entry = path_text if path_text.endswith(suffix) else path_text + suffix
    path = Path(entry).expanduser()
    if not path.is_absolute():
        path = script_dir / path
    return Path(entry).name, str(path.resolve()) if path.exists() else None


def _python_entry(argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    for arg in argv:
        if arg.endswith(".py"):
            path = Path(arg).expanduser()
            if not path.is_absolute():
                path = script_dir / path
            return Path(arg).name, str(path.resolve()) if path.exists() else None
        if arg == "-m":
            return None, None
    return None, None


def _matlab_entry(argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    for index, arg in enumerate(argv):
        if arg in {"-r", "-batch"} and index + 1 < len(argv):
            expression = argv[index + 1].strip()
            candidate = re.split(r"[\s(;]", expression, maxsplit=1)[0].strip("'\"")
            if candidate and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./-]*(?:\.m)?", candidate):
                return _resolve_entry(candidate, script_dir, ".m")
    return None, None


def _entry_for(kind: str, argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    if kind == "python":
        return _python_entry(argv, script_dir)
    if kind == "matlab":
        return _matlab_entry(argv, script_dir)
    return None, None


def find_static_commands(script_text: str, script_dir: Path) -> list[StaticCommand]:
    assignments = _read_assignments(script_text)
    commands = []
    for raw_line in script_text.splitlines():
        line = _strip_inline_comment(raw_line)
        if not line or line.startswith("#") or _assignment_only(line):
            continue
        for shell_command in _split_shell_commands(line):
            tokens = _tokenize(shell_command)
            if not tokens:
                continue
            command = _candidate_command(tokens, assignments)
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


def _command_exists(conn, *, job_id: str, command: StaticCommand, argv_json: str) -> bool:
    row = conn.execute(
        """
        select 1 from job_commands
        where job_id = ?
          and source = 'static-script'
          and kind = ?
          and executable = ?
          and argv = ?
          and coalesce(entry_file, '') = coalesce(?, '')
          and coalesce(entry_file_abs, '') = coalesce(?, '')
        limit 1
        """,
        (job_id, command.kind, command.executable, argv_json, command.entry_file, command.entry_file_abs),
    ).fetchone()
    return row is not None


def _job_file_exists(conn, *, job_id: str, path: str, role: str, source: str) -> bool:
    row = conn.execute(
        """
        select 1 from job_files
        where job_id = ? and path = ? and role = ? and source = ?
        limit 1
        """,
        (job_id, path, role, source),
    ).fetchone()
    return row is not None


def insert_static_commands(conn, *, job_id: str, script_text: str, script_dir: Path, timestamp: str) -> StaticInsertResult:
    inserted_commands = 0
    inserted_files = 0
    seen_entries = set()
    for command in find_static_commands(script_text, script_dir):
        argv_json = json.dumps(command.argv)
        if not _command_exists(conn, job_id=job_id, command=command, argv_json=argv_json):
            conn.execute(
                """
                insert into job_commands (
                  job_id, step_id, time, hostname, cwd, kind, executable, argv,
                  entry_file, entry_file_abs, source
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    None,
                    timestamp,
                    None,
                    str(script_dir),
                    command.kind,
                    command.executable,
                    argv_json,
                    command.entry_file,
                    command.entry_file_abs,
                    "static-script",
                ),
            )
            inserted_commands += 1
        if command.entry_file_abs and command.entry_file_abs not in seen_entries:
            path = Path(command.entry_file_abs)
            if _job_file_exists(conn, job_id=job_id, path=str(path), role="entry_file", source="static-script"):
                seen_entries.add(command.entry_file_abs)
                continue
            conn.execute(
                """
                insert into job_files (job_id, path, relpath, size, role, source, copied, confidence)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(path),
                    path.name,
                    path.stat().st_size if path.exists() else None,
                    "entry_file",
                    "static-script",
                    0,
                    0.8,
                ),
            )
            inserted_files += 1
            seen_entries.add(command.entry_file_abs)
    return StaticInsertResult(commands=inserted_commands, files=inserted_files)
