# python:3.12-slim, pinned by digest for reproducible builds — update
# periodically by checking the current digest for the tag and swapping it in.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rankscale_extract_gcp.py .

# Non-root — script only reads its own files and talks to GCP APIs over
# HTTPS, no write access to /app needed at runtime. Home dir kept (not
# --no-create-home) so local ADC testing can still mount gcloud config
# into it — Docker sets $HOME from this user's /etc/passwd entry.
RUN useradd --create-home --shell /usr/sbin/nologin appuser
USER appuser

ENTRYPOINT ["python", "rankscale_extract_gcp.py"]
