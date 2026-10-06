#!/bin/sh
# Run the relay as its own unprivileged user, not root.
#
# The container starts as root only for long enough to hand /data to that user:
# the volume of every install made before this existed is root-owned, and a file
# written there by a root `docker compose exec` would be too. Anything not owned by
# `splouch` is given to it, then the command runs with root dropped for good.
set -eu

# Numeric ids, the ones the Dockerfile's `useradd` fixes: nothing here depends on
# looking the user up, and `--clear-groups` leaves it no supplementary groups.
APP_UID=10001

if [ "$(id -u)" = 0 ]; then
    if [ -d /data ]; then
        find /data ! -user "$APP_UID" -exec chown "$APP_UID:$APP_UID" {} +
    fi
    exec setpriv --reuid="$APP_UID" --regid="$APP_UID" --clear-groups -- "$@"
fi
exec "$@"
