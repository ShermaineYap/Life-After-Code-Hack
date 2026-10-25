# Downstream KPI dashboard: one image = one data release.
# The warehouse is built and tested by the pipeline (dbt-build job) and copied
# in here, so the bytes that passed the checks are the bytes that ship.
# Locally: `make build` first, then `docker build -t downstream .`
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 \
    WAREHOUSE_PATH=/app/warehouse.duckdb
WORKDIR /app

RUN pip install --no-cache-dir "duckdb>=1.2,<3" "fastapi>=0.115" "uvicorn>=0.30"

COPY dashboard/ /app/dashboard/
COPY build/warehouse.duckdb /app/warehouse.duckdb

RUN useradd --system --uid 10001 app && chown -R app /app
USER app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn dashboard.app:app --host 0.0.0.0 --port ${PORT}"]
