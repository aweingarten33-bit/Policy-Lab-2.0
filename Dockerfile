# --- Stage 1: build the frontend ---
FROM node:20-slim AS frontend-build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend runtime, serving the built frontend ---
FROM python:3.11-slim
WORKDIR /app/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /fe/dist /app/frontend/dist

ENV ENVIRONMENT=production
EXPOSE 8080
# Binds 0.0.0.0 deliberately. Security guidance to default to 127.0.0.1 targets
# servers run directly on a host; inside a container the platform's proxy
# reaches the app over the container network, so binding to localhost would
# make it unreachable rather than safer. Exposure is controlled by the platform
# edge plus the app's own auth middleware, which fails closed in production.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
