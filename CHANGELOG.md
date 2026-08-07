# Changelog

All notable changes are recorded here. The format follows Keep a Changelog and Semantic Versioning.

## [Unreleased]

No unreleased product changes recorded after the `1.0.0-rc.1` candidate snapshot.

## [1.0.0-rc.1] - 2026-07-20

Local release candidate only; no package, image, tag or public deployment was published.

### Added

- Tenant-scoped user, role, permission and audit management in the API and Vue UI.
- Tenant slug login and deterministic handling of duplicate emails across tenants.
- Redis Lua rate limiting with memory/Redis backends and explicit fail-open/fail-closed behavior.
- Persistent document versions, ingestion jobs and knowledge-base reindex jobs with progress, cancel, retry and restart recovery.
- File/URL version updates, Chunk source inspection, active-version reconciliation and revision collection rebuilds.
- Vector collection dimension/count contracts and PostgreSQL/Qdrant compensation paths.
- Vector + BM25 + RRF retrieval diagnostics, deterministic lexical reranking and optional cross-encoder integration.
- Conservative no-evidence answers, prompt-injection Chunk isolation and server-side citation validation.
- SSE request IDs, idempotent reconnect, validated final replacement events and Markdown conversation export.
- Request IDs, redacted JSON logs, live/ready health probes and Prometheus HTTP/retrieval metrics.
- Fixed 31-case retrieval/RAG evaluation with executable thresholds.
- Docker release smoke, k6 fixed-fixture performance test and PostgreSQL/Qdrant/uploads recovery drill.
- Frontend ESLint, Vitest, type checking and responsive admin/document/chat states.

### Changed

- Versioned ingestion now stages new Chunk/vector data and keeps the previous active version on failure.
- Knowledge-base model/dimension changes build a new collection and switch only after exact-count verification.
- Uploads stream to disk while enforcing size and SHA-256 instead of buffering the entire file.
- PDF page metadata now flows from parser through Chunk, retrieval, answer source and UI location.
- Docker defaults now use PostgreSQL 16, Qdrant 1.18.2, Redis 7.4.9, fake Embedding, echo LLM and lexical reranking on ports `19020-19024`.
- Backend containers automatically run Alembic before serving; BM25 tokenization warms before readiness.
- CI now runs backend tests, frontend audit/lint/test/typecheck/build, then starts a clean Compose stack and executes the release chain.

### Fixed

- Removed the superuser cross-tenant authorization bypass.
- Prevented duplicate-email login from selecting an arbitrary tenant.
- Prevented false `[1]` citations and answers generated without reliable evidence.
- Prevented old document versions or failed reindex collections from becoming searchable.
- Added rollback/recovery for database activation, vector write/delete and collection cleanup failures.
- Fixed small-corpus BM25 zero-IDF behavior without weakening the independent evidence threshold.
- Fixed cold first retrieval by moving Jieba initialization into startup readiness.
- Fixed frontend SSE interruption, retry, offline, reconciliation polling and stale-version citation behavior.

### Security

- Added JWT tenant-claim verification and tenant filters across knowledge bases, documents, jobs, versions, chunks, conversations, audit, users and roles.
- Added private/reserved/metadata URL rejection on every redirect, upload content validation and security headers.
- Added production startup rejection for demo credentials, SQLite, memory backends, fail-open rate limiting and Mock providers.
- Added log redaction and request-body/Prompt exclusion.

### Known boundaries

- fake/echo is a Mock engineering path, not real model quality.
- No OCR for scanned PDF and no PDF conversation export.
- Ingestion jobs are persistent but execute in the application process; multiple write workers require an external queue/lease design.
- Public HTTPS, WAF, centralized observability, real provider quality and organization-specific compliance remain deployment-environment checks.

## [0.1.0] - 2026-07-04

### Added

- Initial FastAPI/Vue knowledge-base prototype with file ingestion, hybrid retrieval, SSE chat, basic citations, Docker Compose and Alembic baseline.
