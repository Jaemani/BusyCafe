FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# The web service uses the default command. A worker service overrides this
# with: python -m app.ingest.worker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
