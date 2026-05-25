import shlex
from pathlib import Path


SBATCH_LONG_OPTIONS_WITH_ARG = {
    "account",
    "array",
    "begin",
    "chdir",
    "cluster-constraint",
    "clusters",
    "comment",
    "constraint",
    "container",
    "container-id",
    "cores-per-socket",
    "cpu-bind",
    "cpus-per-gpu",
    "cpus-per-task",
    "dependency",
    "distribution",
    "error",
    "exclude",
    "export",
    "export-file",
    "extra-node-info",
    "gid",
    "gpus",
    "gpus-per-node",
    "gpus-per-socket",
    "gpus-per-task",
    "gres",
    "gres-flags",
    "hint",
    "input",
    "job-name",
    "licenses",
    "mail-type",
    "mail-user",
    "mem",
    "mem-bind",
    "mem-per-cpu",
    "mem-per-gpu",
    "mincpus",
    "nodes",
    "nodelist",
    "ntasks",
    "ntasks-per-core",
    "ntasks-per-gpu",
    "ntasks-per-node",
    "ntasks-per-socket",
    "open-mode",
    "output",
    "partition",
    "qos",
    "reservation",
    "signal",
    "sockets-per-node",
    "threads-per-core",
    "time",
    "time-min",
    "uid",
    "wckey",
    "wrap",
}

SBATCH_SHORT_OPTIONS_WITH_ARG = set("AacCDeEJLNnopqtuwx")


def parse_cli_option(args: list[str], option: str, short_option: str | None = None) -> str | None:
    value = None
    index = 0
    long_name = f"--{option}"
    while index < len(args):
        arg = args[index]
        if arg == "--":
            break
        if arg == long_name and index + 1 < len(args):
            value = args[index + 1]
            index += 2
            continue
        if arg.startswith(long_name + "="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        if short_option and arg == short_option and index + 1 < len(args):
            value = args[index + 1]
            index += 2
            continue
        if short_option and arg.startswith(short_option) and len(arg) > len(short_option):
            value = arg[len(short_option) :]
            index += 1
            continue
        index += 1
    return value


def parse_script_option(script_text: str, option: str, short_option: str | None = None) -> str | None:
    value = None
    for line in script_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#SBATCH"):
            continue
        body = stripped[len("#SBATCH") :].strip()
        try:
            args = shlex.split(body)
        except ValueError:
            args = body.split()
        parsed = parse_cli_option(args, option, short_option)
        if parsed is not None:
            value = parsed
    return value


def metadata_value(
    script_text: str,
    passthrough_args: list[str],
    option: str,
    short_option: str | None = None,
) -> str | None:
    return parse_cli_option(passthrough_args, option, short_option) or parse_script_option(
        script_text, option, short_option
    )


def parse_command_args(command: str, *, executable: str = "csbatch") -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    if not parts:
        return []
    if Path(parts[0]).name == executable:
        return parts[1:]
    return parts


def command_text(executable: str, argv: list[str]) -> str:
    return shlex.join([executable, *argv])
