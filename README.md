# The Policy Lab

The Policy Lab is a source-grounded compliance policy analysis and drafting application. It combines deterministic application logic, regulatory retrieval, post-generation verification, and LLM reasoning rather than relying on a single prompt-and-response call.

## Architecture

- **Frontend:** React + TypeScript + Vite
- **Backend:** FastAPI / Python 3.11
- **LLM abstraction:** LiteLLM provider cascade
- **Regulatory retrieval:** Chroma-backed knowledge base populated from authoritative source material
- **Verification:** citation checks, evidence records, semantic claim-support classification, unsupported-specific checks, and obligation classification
- **Deployment:** multi-stage Docker image serving the built frontend from FastAPI

The normal analysis flow is:

1. Accept policy text and industry/jurisdiction context.
2. Retrieve relevant regulatory material.
3. Refuse generation in production when authoritative grounding is unavailable.
4. Generate the gap analysis with retrieved context.
5. Build evidence for findings and verify claim support.
6. Reclassify claimed legal mandates when the cited source does not establish the obligation.
7. Reconcile final package-level verification metadata before serving the result.

## Correctness invariants

These are product requirements, not optional implementation details:

- Production analysis must not run without authoritative grounding.
- A real citation is not sufficient by itself; the cited material must support the claim being made.
- Guidance, best practice, organizational choice, and legal requirements must remain distinguishable.
- A failed verification pass must never be presented as fully verified output.
- Background-job reads, streams, and cancellation must remain bound to the client session that created the job.
- Knowledge-base mutation requires the separate administrator credential.
- Uploaded policy text is not persisted to a database or disk by the analysis endpoints. Temporary background-job output is held in process memory and expires.

When changing orchestration, streaming, verification, or job code, add a test against the **actual route/path used by the application**. A unit test for an unused helper does not protect production behavior.

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

At least one supported LLM provider key is required for model-backed features. Keep `ENVIRONMENT=development` locally.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

The frontend defaults to the same-origin API in production. Set `VITE_API_URL` only when development requires a separate backend origin.

## Tests

Backend:

```bash
cd backend
python -m pytest tests/ -q
```

Frontend type-check and build:

```bash
cd frontend
npx tsc --noEmit -p tsconfig.json
npm run build
```

GitHub Actions is configured to run the backend suite, frontend type-check/build, and advisory dependency audits on pushes and pull requests.

## Production security model

The production Docker image sets `ENVIRONMENT=production` and `REQUIRE_GROUNDING=true`.

- `API_KEY` protects non-public API routes and fails closed when missing in production.
- `ADMIN_API_KEY` is separately required for destructive knowledge-base operations.
- `/api/health` remains public for platform health checks.
- Diagnostics and OpenAPI documentation are not anonymously exposed in production.
- Security headers include CSP, frame protection, content-type protection, referrer policy, and HSTS in production.
- Expensive endpoints are rate limited in-process. A shared rate-limit store is required before horizontal scaling.

## Deployment note

The Docker build bakes the regulatory knowledge base into the image and is configured to fail if required regulatory chunks cannot be built. Runtime seeding is intentionally disabled by default because embedding the corpus can exceed the memory budget of a small serving instance.

## Current scaling limits

The current job stores and rate limiter are process-local. That is appropriate for a single application instance, but before multi-instance or enterprise deployment they should move to shared infrastructure such as Redis or a durable job system. The shared app-password model should also be replaced with real user/organization authentication and authorization before handling multiple independent customer organizations.

## Disclaimer

The Policy Lab is a compliance-support tool. Generated findings, suggested policy language, and source verification should still be reviewed by qualified compliance/legal personnel before implementation.
