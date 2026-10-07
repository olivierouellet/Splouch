FROM python:3.13-slim

WORKDIR /app

COPY cloud/requirements.txt requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Cloud server code, templates, and shared locales.
#
# Every cloud_*.py, not just cloud_server.py: the app is several flat modules that
# import each other by bare name (cloud_paths, cloud_bus, cloud_auth, …), and a
# glob means adding one does not need a Dockerfile edit to go with it. The `cloud_`
# prefix is what keeps them from colliding with the Pi server's same-named modules
# when both directories are on sys.path during a test run.
#
# `cloud_*` also draws the line in the right place on its own: deploy_webhook.py
# does not match, and it should not — it runs on the host, beside docker, not in
# this container.
COPY cloud/cloud_*.py ./
# Python shared with the Pi server — splouch_i18n, the single copy of the label,
# locale and palette rules both servers answer from. Flat beside the cloud modules
# so a plain `import splouch_i18n` finds it; cloud_paths adds the from-source
# location instead when running outside the image.
COPY shared/py/ ./
COPY cloud/templates/ templates/
COPY shared/templates/ templates_shared/
COPY shared/locales/ locales/

# Shared static assets (single canonical copy in shared/, also used by the Pi server)
COPY shared/static/css/  static/css/
COPY shared/static/fonts/ static/fonts/
COPY shared/static/icons/ static/icons/
COPY shared/static/img/  static/img/
COPY shared/static/js/   static/js/

# The app's own user (cloud/docker-entrypoint.sh drops to it). A fixed uid, so the
# data volume's ownership means the same thing in every image version.
RUN groupadd --system --gid 10001 splouch \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /data splouch \
    && mkdir -p /data && chown splouch:splouch /data
COPY --chmod=755 cloud/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

# The commit this image was built from, which a deploy checks against its checkout
# (cloud_deploy.stale_image). Last, so a new commit rebuilds no layer above it.
ARG SPLOUCH_COMMIT=""
LABEL org.opencontainers.image.revision=$SPLOUCH_COMMIT

# No access log: uvicorn's default writes every request with the visitor's IP (the
# real one, through Caddy's X-Forwarded-For) to `docker compose logs`, kept for as
# long as the container is. `/privacy` says this server keeps no request logs.
CMD ["uvicorn", "cloud_server:app", "--host", "0.0.0.0", "--port", "5000", "--no-access-log"]
