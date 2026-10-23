"""Opt-in browser integration; only fake SSH and isolated data are used."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_web import web_db, server
from conftest import write_executable


@pytest.mark.skipif(os.environ.get('CSLURM_BROWSER_TEST') != '1', reason='opt-in Playwright verification')
def test_remote_browser_workflows(server, web_db, fake_bin, tmp_path):
    ssh_config = Path.home() / '.ssh'
    ssh_config.mkdir()
    (ssh_config / 'config').write_text('Host cluster\nHost missing\nHost *\n')
    (fake_bin / 'python3').symlink_to(sys.executable)
    remote = tmp_path / 'remote-data'
    environment = {**os.environ, 'CSLURM_ROOT': str(remote)}
    subprocess.run([sys.executable, '-c', 'from cslurm.db import connect,init_db\n'
                    'with connect() as c:\n init_db(c)\n c.execute("insert into jobs(job_id,job_name,state) values (\'900\',\'Remote task\',\'COMPLETED\')")'],
                   env=environment, check=True)
    write_executable(fake_bin / 'ssh', f'#!{sys.executable}\n'
                     'import os,shlex,sys\na=shlex.split(sys.argv[-1]); a.pop(0)\n'
                     f'if sys.argv[-2]=="missing" and a[0]=={sys.executable!r}:\n'
                     ' os.environ.pop("PYTHONPATH",None); a.insert(1,"-S")\n'
                     f'elif a[0] not in ({sys.executable!r},"/bin/sh"): os.environ.pop("PYTHONPATH",None)\n'
                     'os.execv(a[0],a)\n')
    env = {**os.environ, 'CSLURM_TEST_URL': f'http://127.0.0.1:{server.server_port}/#token={server.token}',
           'CSLURM_TEST_PYTHON': sys.executable, 'CSLURM_TEST_REMOTE': str(remote),
           'CSLURM_TEST_INSTALL': str(Path.home() / 'browser-install'),
           'CSLURM_TEST_NEW_ROOT': str(Path.home() / 'browser-data'),
           'CSLURM_SCREENSHOTS': str(tmp_path)}
    subprocess.run([os.environ.get('NODE', 'node'), str(Path(__file__).parent / 'web_remote_browser.cjs')],
                   env=env, check=True, timeout=180)
