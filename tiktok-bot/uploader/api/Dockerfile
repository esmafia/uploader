# Build: `docker build -f api/Dockerfile -t tiktok-api .` from the repo root.
# Depends on the local `tiktok-base` image built from docker/base.Dockerfile
# by docker-compose (see docker-compose.yml).
FROM tiktok-base:latest

WORKDIR /app

COPY api /app/api

EXPOSE 8000

# Reload disabled in prod; enable via --reload when developing.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
