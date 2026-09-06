FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY requirements.lock ./
COPY harnesses ./harnesses
COPY src ./src

RUN pip install --no-cache-dir ".[api]" \
    && useradd --create-home --uid 10001 syndicate \
    && mkdir -p /app/.syndicate/lineage \
    && chown -R syndicate:syndicate /app

# Pin must match syndicate.repositories.benchmark_manifest.ITSMBENCH_REVISION.
ARG ITSMBENCH_REVISION=30da7457d5479d0bcfae40dece7bd85d66df4401
USER root
RUN git clone https://github.com/new-measure/ITSMBench.git /app/benchmark/ITSMBench \
    && git -C /app/benchmark/ITSMBench checkout --detach "$ITSMBENCH_REVISION" \
    && chown -R syndicate:syndicate /app/benchmark

USER syndicate
EXPOSE 8000
CMD ["uvicorn", "syndicate.controllers.http:app", "--host", "0.0.0.0", "--port", "8000"]
