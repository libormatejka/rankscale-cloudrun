# python:3.12-slim, pinned by digest for reproducible builds — update
# periodically by checking the current digest for the tag and swapping it in.
FROM python:3.12-slim@sha256:e5c9fa26ffb76e11e0f054f30dc2523a2f9693f0c36c0cf1e39b27e152d899fc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rankscale_extract_gcp.py .

ENTRYPOINT ["python", "rankscale_extract_gcp.py"]
