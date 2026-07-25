FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN useradd --create-home --uid 10001 appuser

COPY requirements-py312.lock /app/requirements-py312.lock
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements-py312.lock

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY scripts /app/scripts
RUN python -m pip install --no-deps . \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import macro_b3_bot; print('ok')"
ENTRYPOINT ["macro-b3"]
