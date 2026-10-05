"""Dump and restore the control plane's store, by hand.

The `backup` service dumps every night on its own (docker-compose.yml). This is for
the times you need one now — before moving the control plane to another server,
or to bring one back:

    python3 cloud_backup.py dump [FILE]       # default /var/backups/splouch/now
    python3 cloud_backup.py restore FILE      # replaces the store with FILE

Moving the control plane (docs/cloud.md): `dump` on the old server, copy the file,
install the new server with the control-plane part, `restore` there, then point
the domain at it. Pis and apps only know the domain, so nothing else changes.

Runs on the host, standard library only; talks to the `postgres` container.
"""

import datetime
import os
import subprocess
import sys

import cloud_deploy
import cloud_workers

BACKUP_DIR = "/var/backups/splouch"


def _postgres(*args, env):
    argv, cenv = cloud_deploy._compose("exec", "-T", "postgres", *args, env=env)
    return argv, cenv


def dump(path=None, runner=subprocess.run):
    """Write a custom-format dump to `path`. Returns 0 or the failing exit code."""
    env = cloud_workers.read_env()
    path = path or os.path.join(
        BACKUP_DIR, f"splouch-{datetime.datetime.now():%Y-%m-%d-%H%M}.dump"
    )
    argv, cenv = _postgres("pg_dump", "-U", "splouch", "-Fc", "splouch", env=env)
    part = path + ".part"
    with open(part, "wb") as out:
        code = runner(argv, env=cenv, cwd=cloud_deploy.HERE, stdout=out, check=False)
    if code.returncode:
        os.remove(part)
        return code.returncode
    os.replace(part, path)
    print(path, flush=True)
    return 0


def restore(path, runner=subprocess.run):
    """Replace the store with the dump at `path`."""
    env = cloud_workers.read_env()
    argv, cenv = _postgres(
        "pg_restore", "-U", "splouch", "-d", "splouch", "--clean", "--if-exists",
        "--no-owner", env=env,
    )  # fmt: skip
    with open(path, "rb") as src:
        return runner(
            argv, env=cenv, cwd=cloud_deploy.HERE, stdin=src, check=False
        ).returncode


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["dump"] and len(args) <= 2:
        sys.exit(dump(args[1] if len(args) == 2 else None))
    if args[:1] == ["restore"] and len(args) == 2:
        sys.exit(restore(args[1]))
    sys.exit("usage: cloud_backup.py dump [FILE] | restore FILE")
