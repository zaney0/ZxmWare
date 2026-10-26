# ZxmWare API server image.
#
# The large served content (firmware .tgz bundles, .fap apps) is NOT baked into the
# image -- it is bind-mounted at runtime into /srv/data (see docker-compose.yml).
# Only application code and the indexer ship in the image.

FROM python:3.12-slim

# Don't buffer stdout/stderr (so logs appear immediately) and don't write .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/srv/data

WORKDIR /srv

# Install dependencies first so Docker can cache this layer across code changes.
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt

# Application code + the (separately-maintained) indexer.
COPY app /srv/app
COPY indexer /srv/indexer

# data/ is a mounted volume, not copied. Declare it so the intent is explicit.
VOLUME ["/srv/data"]

EXPOSE 8000

# A simple liveness check against the /health endpoint.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
