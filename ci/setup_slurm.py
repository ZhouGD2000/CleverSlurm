"""Disposable GitHub runner only: single-node Slurm with real accounting."""
import os
import pwd
import secrets
import socket
import subprocess
import time
from pathlib import Path


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise SystemExit('Only run as root on a disposable GitHub Actions runner')
    host = socket.gethostname()
    owner = pwd.getpwnam('slurm')
    for name in ['state', 'spool', 'log']:
        path = Path('/var/lib/cleverslurm-ci') / name
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, owner.pw_uid, owner.pw_gid)
    password = secrets.token_hex(24)
    run('systemctl', 'start', 'munge', 'mariadb')
    run('mysql', input=f"CREATE DATABASE IF NOT EXISTS slurm_ci; CREATE USER 'slurm_ci'@'localhost' IDENTIFIED BY '{password}'; GRANT ALL ON slurm_ci.* TO 'slurm_ci'@'localhost';")
    hardware = subprocess.check_output(['slurmd', '-C'], text=True).splitlines()[0]
    hardware = ' '.join(part for part in hardware.split() if not part.startswith('RealMemory='))
    configs = {
        'slurmdbd.conf': f'''AuthType=auth/munge
DbdHost=localhost
SlurmUser=slurm
StorageType=accounting_storage/mysql
StorageHost=localhost
StorageUser=slurm_ci
StoragePass={password}
StorageLoc=slurm_ci
LogFile=/var/lib/cleverslurm-ci/log/slurmdbd.log
PidFile=/run/slurmdbd.pid
''',
        'slurm.conf': f'''ClusterName=ci
SlurmctldHost={host}
SlurmUser=slurm
AuthType=auth/munge
StateSaveLocation=/var/lib/cleverslurm-ci/state
SlurmdSpoolDir=/var/lib/cleverslurm-ci/spool
SlurmctldLogFile=/var/lib/cleverslurm-ci/log/slurmctld.log
SlurmdLogFile=/var/lib/cleverslurm-ci/log/slurmd.log
SlurmctldPidFile=/run/slurmctld.pid
SlurmdPidFile=/run/slurmd.pid
ProctrackType=proctrack/pgid
TaskPlugin=task/none
SelectType=select/cons_tres
SelectTypeParameters=CR_CPU
ReturnToService=2
AccountingStorageType=accounting_storage/slurmdbd
AccountingStorageHost=localhost
JobAcctGatherType=jobacct_gather/linux
JobAcctGatherFrequency=1
{hardware} RealMemory=1024 NodeAddr=127.0.0.1 State=UNKNOWN
PartitionName=debug Nodes={host} Default=YES MaxTime=5 State=UP
'''}
    for name, content in configs.items():
        path = Path('/etc/slurm') / name
        with path.open('x') as stream:
            stream.write(content)
        os.chmod(path, 0o600 if name == 'slurmdbd.conf' else 0o644)
        os.chown(path, owner.pw_uid, owner.pw_gid)
    run('systemctl', 'start', 'slurmdbd')
    for attempt in range(30):
        result = subprocess.run(['sacctmgr', '-n', 'show', 'cluster'], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('slurmdbd did not become ready')
    run('sacctmgr', '-i', 'add', 'cluster', 'ci')
    run('systemctl', 'start', 'slurmctld', 'slurmd')
    for attempt in range(30):
        result = subprocess.run(['sinfo', '-h', '-o', '%t'], capture_output=True, text=True)
        if result.returncode == 0 and 'idle' in result.stdout:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Slurm node did not become idle')


if __name__ == '__main__':
    main()
