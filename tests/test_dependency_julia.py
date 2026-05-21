from cslurm.collect.dependency_julia import find_julia_dependencies


def test_julia_include_parser_finds_static_local_includes(tmp_path):
    entry = tmp_path / "run.jl"
    solver = tmp_path / "src" / "solver.jl"
    model = tmp_path / "models" / "hubbard.jl"
    solver.parent.mkdir()
    model.parent.mkdir()
    entry.write_text('include("src/solver.jl")\ninclude(joinpath("models", "hubbard.jl"))\n')
    solver.write_text("# solver\n")
    model.write_text("# model\n")

    deps = find_julia_dependencies(entry)

    assert deps == {solver, model}
