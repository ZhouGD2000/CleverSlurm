"""Build an offline wheel from this installed package, never from remote Git HEAD."""

import json
import shutil
import subprocess
import sys
from importlib.metadata import entry_points, version
from pathlib import Path

import cslurm


def build_wheel(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = directory / 'source'
    project.mkdir()
    shutil.copytree(Path(cslurm.__file__).parent, project / 'src' / 'cslurm',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
    scripts = {ep.name: ep.value for ep in entry_points(group='console_scripts')
               if ep.value.startswith('cslurm.cli.')}
    if not scripts:
        raise RuntimeError('Install CleverSlurm in this Python environment before building remote installers')
    metadata = ('[build-system]\nrequires=["setuptools", "wheel"]\n'
                'build-backend="setuptools.build_meta"\n[project]\nname="CleverSlurm"\n'
                f'version={json.dumps(version("CleverSlurm"))}\nrequires-python=">=3.10"\n'
                '[project.scripts]\n' + ''.join(f'{json.dumps(k)}={json.dumps(v)}\n' for k, v in scripts.items()) +
                '[tool.setuptools.packages.find]\nwhere=["src"]\n'
                '[tool.setuptools.package-data]\n"cslurm.web"=["static/*", "static/icons/*"]\n')
    (project / 'pyproject.toml').write_text(metadata)
    with (directory / 'build.log').open('xb') as log:
        result = subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-index', '--no-deps',
                                 '--no-build-isolation', '--wheel-dir', str(directory), str(project)],
                                stdout=log, stderr=log, timeout=120)
    if result.returncode:
        raise RuntimeError('Local wheel build failed. Install pip, setuptools and wheel in the cweb environment; build.log is retained')
    wheels = list(directory.glob('*.whl'))
    if len(wheels) != 1 or wheels[0].stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError('Unexpected local wheel artifact')
    return wheels[0]
