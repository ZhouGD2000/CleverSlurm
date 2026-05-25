from cslurm.slurm.sbatch_options import (
    command_text,
    metadata_value,
    parse_cli_option,
    parse_command_args,
    parse_script_option,
)


def test_parse_script_option_accepts_common_sbatch_forms():
    script_text = "\n".join(
        [
            "#!/bin/bash",
            "#SBATCH --job-name=from-script",
            "#SBATCH -p CPU2",
            "#SBATCH -N2",
            "hostname",
        ]
    )

    assert parse_script_option(script_text, "job-name", "-J") == "from-script"
    assert parse_script_option(script_text, "partition", "-p") == "CPU2"
    assert parse_script_option(script_text, "nodes", "-N") == "2"


def test_parse_cli_option_uses_last_value_like_sbatch():
    args = ["--job-name", "old", "-Jnew", "--partition=CPU1", "-p", "CPU2"]

    assert parse_cli_option(args, "job-name", "-J") == "new"
    assert parse_cli_option(args, "partition", "-p") == "CPU2"


def test_metadata_value_prefers_cli_over_script():
    script_text = "#SBATCH --job-name=from-script\n#SBATCH --partition=CPU1\n"
    args = ["--job-name", "from-cli", "-pCPU2"]

    assert metadata_value(script_text, args, "job-name", "-J") == "from-cli"
    assert metadata_value(script_text, args, "partition", "-p") == "CPU2"


def test_parse_recorded_command_handles_quoted_arguments():
    command = command_text("csbatch", ["--job-name", "long name", "-p", "CPU2", "job.slurm"])

    args = parse_command_args(command, executable="csbatch")

    assert args == ["--job-name", "long name", "-p", "CPU2", "job.slurm"]
    assert parse_cli_option(args, "job-name", "-J") == "long name"
