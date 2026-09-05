# Build once from the task's original main image; pin the resulting image ID in trials.
ARG TASK_IMAGE
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM ${TASK_IMAGE}
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git procps util-linux \
    && rm -rf /var/lib/apt/lists/*
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PYTHON=3.13.7
RUN uv python install 3.13.7 && uv venv --python 3.13.7 /opt/syndicate-venv
ENV VIRTUAL_ENV=/opt/syndicate-venv PATH=/opt/syndicate-venv/bin:$PATH
WORKDIR /opt/syndicate
COPY requirements.lock pyproject.toml README.md ./
RUN uv pip sync requirements.lock
COPY src/syndicate ./src/syndicate
RUN uv pip install --no-deps --no-build-isolation . \
    && python -c 'from syndicate.runtime_contracts import installed_runtime; print(installed_runtime().model_dump_json())' \
    && useradd --uid 10001 --create-home --shell /bin/bash syndicate \
    && mkdir -p /app /logs/agent && chown 10001:10001 /app /logs/agent
WORKDIR /app
