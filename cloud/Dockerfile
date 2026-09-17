FROM python:3.13-slim

WORKDIR /app

COPY cloud/requirements.txt requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Cloud server code, templates, and shared locales
COPY cloud/cloud_server.py .
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
