# Builder stage
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1

WORKDIR /build

COPY pyproject.toml ./
COPY src/ src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir . && \
    /opt/venv/bin/pip install --no-cache-dir alembic

# Runtime stage
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY alembic/ alembic/
COPY src/ src/
COPY pyproject.toml ./

WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "dropbox.main:app", "--host", "0.0.0.0", "--port", "8000"]
