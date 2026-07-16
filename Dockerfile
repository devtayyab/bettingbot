FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps for psycopg + playwright chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

# Install Playwright browser only in images that need it (placement worker)
ARG INSTALL_BROWSERS=false
RUN if [ "$INSTALL_BROWSERS" = "true" ]; then playwright install --with-deps chromium; fi

EXPOSE 8000
CMD ["uvicorn", "valuebet.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
