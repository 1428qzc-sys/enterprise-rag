# Security Policy

## Supported version

| Version | Status |
| --- | --- |
| `1.0.0-rc.1` | Supported release candidate |
| `<1.0.0-rc.1` | Historical; upgrade before reporting compatibility defects |

## Reporting a vulnerability

Do not open a public issue containing credentials, customer documents, exploit payloads or cross-tenant identifiers. If the repository is hosted on GitHub, use a private Security Advisory; otherwise contact the repository owner through a private channel and include:

- affected version and deployment mode;
- minimal reproduction steps;
- expected and observed authorization boundary;
- request IDs and redacted logs;
- whether data was read, modified or deleted.

Do not include real JWTs, API keys, passwords, document bodies or personal data. Cross-tenant access, credential disclosure, arbitrary SSRF and data/vector corruption are treated as P0/P1 until disproved.

## Implemented controls

### Authentication and authorization

- Passwords use salted PBKDF2-HMAC-SHA256 with 210,000 iterations.
- Access tokens are signed HS256 JWTs with user, tenant, issue and expiry claims. The API reloads the active user and tenant on each authenticated request and verifies the tenant claim.
- Permissions are enforced by backend dependencies; hiding a frontend control is not an authorization decision.
- Knowledge bases, documents, versions, jobs, chunks, conversations, audit records, users and roles are filtered by `tenant_id`. Objects in another tenant return 404 to avoid identifier disclosure.
- `is_superuser` means full permissions inside the user’s own tenant; it does not bypass tenant filtering.
- System roles, the current account and the last active tenant administrator have destructive-operation protection.

### Input and network controls

- Uploads are streamed to staging storage with a byte limit and SHA-256 calculation.
- PDF magic, DOCX/XLSX ZIP structure and text/binary content are validated; extension alone is not trusted.
- URL ingestion allows only HTTP/HTTPS, resolves and checks every target, rejects loopback/private/link-local/reserved/metadata addresses, revalidates redirects, and limits time, redirects and response size.
- Nginx and FastAPI return CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, Referrer Policy and Permissions Policy headers.

### Abuse and isolation controls

- Compose uses Redis-backed atomic Lua rate limiting. Authenticated requests are keyed by tenant; anonymous requests by client IP.
- Production mode requires Redis `fail_closed`; development can explicitly use memory or fail-open behavior.
- Request handling has a total timeout and returns a request ID on 500/504 responses.
- Audit events cover authentication and administrative/document operations without storing secrets.

### RAG-specific controls

- Vector payloads include tenant, knowledge base, document and version identifiers; database source hydration applies the same tenant and active-version filters.
- New document versions are staged and only become active after vectors and database state are ready. Compensation preserves the old active version when activation fails.
- Answers are generated only when evidence passes a fixed threshold. No evidence produces a deterministic “不知道” response.
- Citation indices are checked against actual retrieved sources before persistence; invalid or missing citations are rejected.
- Known prompt-injection patterns in source chunks are marked and excluded from generation context; fixed evaluation includes injection and cross-tenant cases.

### Secrets and production startup

- `.env` is ignored; `.env.example` contains only local demo values.
- JSON logging redacts bearer tokens, secret-like key/value pairs and emails, and never logs request bodies or full prompts.
- `ENVIRONMENT=production` rejects demo credentials, SQLite, memory vector/rate-limit backends, fail-open rate limiting, Mock providers and missing OpenAI-compatible keys.

## Deployment responsibilities

The repository does not provide a public security boundary by itself. A production operator must additionally provide:

- HTTPS, trusted proxy configuration, WAF or equivalent edge controls;
- secret manager integration and rotation;
- private network access to PostgreSQL, Qdrant, Redis and `/metrics`;
- database/Qdrant encryption and backups appropriate to the data classification;
- centralized audit/log retention and alerting;
- malware scanning if untrusted users can upload files;
- provider-specific privacy, residency and retention review.

## Known security boundaries

- Access tokens are stored in browser local storage and are not refresh tokens. XSS would expose the token; the CSP and dependency controls reduce but do not eliminate that risk. High-assurance deployments should move to a reviewed secure-cookie/session design.
- Tokens cannot be individually revoked before expiry; disabling the user or tenant blocks subsequent API authorization because the principal is reloaded from the database.
- File validation is structural, not antivirus or content-disarm-and-reconstruction.
- Prompt-injection detection is a defense-in-depth heuristic, not a proof that arbitrary malicious prose is safe.
- Default Mock mode must never be used to make claims about external model security or quality.

## Verification

Security regression tests cover unauthorized access, cross-tenant enumeration/read/update/delete matrices, JWT tenant claims, roles, Redis failure policies, SSRF, malicious redirects, file structures, prompt injection, citation validity, logging redaction and production config rejection.

Release checks also run dependency audits, a repository secret scan and `git diff --check`. See [RUNBOOK.md](RUNBOOK.md) for incident and recovery handling and [MULTI_TENANCY.md](MULTI_TENANCY.md) for the authorization model.
