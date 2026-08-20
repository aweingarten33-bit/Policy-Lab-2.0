# --- Stage 1: build the frontend ---
FROM node:20-slim AS frontend-build
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: backend runtime, serving the built frontend ---
FROM python:3.14-slim
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

# --- Bake the knowledge base into the image ---
# Downloading and embedding regulations at container START meant redoing the
# work on every deploy (ephemeral filesystem) and blocking the app past the
# platform health-check window, so the corpus never finished building. Doing it
# once here means containers boot instantly against a complete, already-embedded
# store with no network dependency. Runtime seeding remains as a fallback.
#
# Warns rather than fails on a transient eCFR outage; pass --require-success to
# make an empty knowledge base a hard build failure instead.
#
# Bounded twice on purpose. Seeding is the slowest, most memory-hungry and most
# network-dependent step in the build, and shipping an image matters more than
# shipping a complete corpus: the bundled OIG/HCCA guidance loads from disk
# regardless, so a capped run still yields real grounding. `timeout` covers a
# hang the app's own budget cannot see, and `|| true` keeps any failure here
# from blocking a deploy.
ENV KB_SEED_TIMEOUT_SECONDS=360
RUN timeout 600 python scripts/build_knowledge_base.py || true

ENV ENVIRONMENT=production
EXPOSE 8080
# Binds 0.0.0.0 deliberately. Security guidance to default to 127.0.0.1 targets
# servers run directly on a host; inside a container the platform's proxy
# reaches the app over the container network, so binding to localhost would
# make it unreachable rather than safer. Exposure is controlled by the platform
# edge plus the app's own auth middleware, which fails closed in production.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
