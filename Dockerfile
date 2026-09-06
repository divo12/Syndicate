FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir ".[api]"

EXPOSE 8000
CMD ["uvicorn", "syndicate.controllers.http:app", "--host", "0.0.0.0", "--port", "8000"]
