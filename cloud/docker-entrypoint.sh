#!/bin/sh
# Run the relay as its own unprivileged user, not root.
#
# The container starts as root only for long enough to hand /data to that user:
# the volume of every install made before this existed is root-owned, and a file
# written there by a root `docker compose exec` would be too. Anything not owned by
# `splouch` is given to it, then the command runs with root dropped for good.
set -eu

if [ "$(id -u)" = 0 ]; then
    if [ -d /data ]; then
        find /data ! -user splouch -exec chown splouch:splouch {} +
    fi
    exec setpriv --reuid=splouch --regid=splouch --init-groups -- "$@"
fi
exec "$@"
