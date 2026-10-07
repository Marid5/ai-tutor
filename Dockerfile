# syntax=docker/dockerfile:1

# Stage 1: build the web client.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: the backend, serving the built client.
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY migrations/ migrations/
COPY content/ content/
COPY scripts/validate_content.py scripts/validate_content.py
COPY VERSION ./
COPY --from=web /web/dist/ static/

# A build with broken content fails here instead of at startup.
RUN python scripts/validate_content.py --quiet

# The database lives in a mounted volume owned by the unprivileged user.
RUN mkdir -p /app/data && chown 10001:10001 /app/data

ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA}

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# The reverse proxy is the only client that may set X-Forwarded-For; the app
# reads it itself when TRUST_PROXY=true, so uvicorn must not rewrite the peer.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
