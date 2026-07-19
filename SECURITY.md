# Security Policy

## Supported Versions

**Enterprise RAG** is actively maintained on the `main` branch. Security fixes target `main` first.

| Version | Supported |
| --- | --- |
| Latest on `main` | Yes |
| Older tags | Best effort |

## Reporting a Vulnerability

Do **not** open a public issue with exploit details, credentials, or proof-of-concept code.

Preferred channels:

1. GitHub **Private vulnerability reporting** or a **Security Advisory** for this repository, if enabled.
2. If private reporting is unavailable, open a public issue asking for a private contact channel **without** technical exploit details.

Include affected version/commit, reproduction steps, impact, and relevant logs with secrets removed.

## Scope & Hardening

Production deployments must override default bootstrap credentials and `AUTH_SECRET_KEY`. See [DEPLOYMENT.md](DEPLOYMENT.md) §6 for the security baseline env vars.

Detailed audit findings, fixes, and remaining risks: [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

Load and performance validation: [PERFORMANCE_REPORT.md](PERFORMANCE_REPORT.md).
