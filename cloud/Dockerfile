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
COPY cloud/templates/ templates/
COPY shared/templates/ templates_shared/
COPY shared/locales/ locales/

# Shared static assets (single canonical copy in shared/, also used by the Pi server)
COPY shared/static/css/  static/css/
COPY shared/static/fonts/ static/fonts/
COPY shared/static/icons/ static/icons/
COPY shared/static/img/  static/img/
COPY shared/static/js/   static/js/

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
EXPOSE 5000

CMD ["uvicorn", "cloud_server:app", "--host", "0.0.0.0", "--port", "5000"]
