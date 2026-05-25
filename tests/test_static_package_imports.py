def test_legacy_static_imports_still_forward_to_static_package():
    from cslurm.collect.dependency_julia import find_julia_dependencies as legacy_julia
    from cslurm.collect.static import find_julia_dependencies, find_static_commands, insert_static_commands
    from cslurm.collect.static_script import find_static_commands as legacy_script
    from cslurm.collect.static_script import insert_static_commands as legacy_insert

    assert legacy_julia is find_julia_dependencies
    assert legacy_script is find_static_commands
    assert legacy_insert is insert_static_commands
