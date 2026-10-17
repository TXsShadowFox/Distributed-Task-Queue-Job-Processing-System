# Security review

A review of FlowForge's security controls, the automated scans that were run, and what was found. Dates are the
dates of the runs recorded below; repeat the scans with `make security-check` (CI runs them on every push).

## Automated scans (2026-10-14)

| Scan | Scope | Result |
|---|---|---|
| `pip-audit --strict` | All 811 locked runtime Python requirements (`uv export --no-dev`, hashed) | **No known vulnerabilities** |
| `bandit` | All shipped Python packages (12,326 lines) | **0 issues** after triage: 6 low-severity findings, all false positives (below) |
| `npm audit --omit=dev` | Dashboard production dependencies | **0 vulnerabilities** after moving a build-time package to dev dependencies (below) |
| `npm audit` (all) | Dashboard including dev and build tooling | 9 high, all one advisory in dev-only tooling (below) |
| `ruff` security rules (`S`) | All Python code | Pass; the exceptions are commented in code |

### Bandit triage

All six findings were false positives. Each line now carries a `# nosec` comment that names the reason; the rules
themselves stay enabled.

| Finding | Location | Why it is safe |
|---|---|---|
| B105 hard-coded password `None` | `services/tasks.py`, `"lease_token": None` | Clears a lease when a task finishes; not a credential |
| B105 hard-coded password | `cli.py`, `PASSWORD_ENV = "FLOWFORGE_ADMIN_PASSWORD"` | The *name* of the environment variable the password is read from |
| B311 `random` | SDK retry jitter | Spreading retries needs no cryptographic randomness |
| B311 `random` | `business.py` (×2) | Seeded generators for deterministic synthetic demo data |
| B311 `random` | `demo.py`, `flaky` | Simulated failure rate of a demo handler |

Cryptographic randomness (tokens, keys, lease tokens, refresh tokens) uses `secrets` and `uuid4`.

### npm advisory GHSA-vfj7-8cjw-p6xm (`braces`)

`braces` ≤ 3.0.3 is vulnerable to stack exhaustion with deeply nested brace patterns (CVSS 7.5). No fixed release
exists; 3.0.3 is the latest version. It reaches the dashboard only through glob matching in build and development
tools: the `shadcn` CLI, `ts-morph` and Next.js's ESLint plugin. Those tools expand patterns from their own
configuration, never from user input.

* **Fix:** `shadcn` was listed under `dependencies` although it is only used at build time (the CLI, plus a CSS file
  that Tailwind compiles). It now sits in `devDependencies`, and the production audit is clean.
* **Verification:** the standalone production server bundle (what the Docker image ships) contains no `shadcn`,
  `braces`, `micromatch` or `fast-glob` (checked after `next build`).
* **Remaining risk:** none at runtime. CI audits production dependencies with `--audit-level=high`, so a future
  runtime-reachable advisory fails the build.

## Controls reviewed

### Authentication
* Passwords: Argon2id, a minimum of 10 characters, rejected if equal to the email or too repetitive. Failed logins
  are counted per account, which then locks temporarily (`login_max_failures`, `login_lockout_seconds`). The login
  error does not reveal whether the email exists.
* Access tokens: HS256 JWTs (issuer and audience checked, 15-minute lifetime), with a signing secret of at least 32
  characters enforced at start-up. Tokens issued before a password change or reset are rejected.
* Refresh tokens: random and stored hashed, in an `httpOnly`, `SameSite=Strict` cookie scoped to the auth path and
  `Secure` by default. Every refresh rotates the token; reusing a rotated token revokes the whole family and is
  audited (`session.refresh_reuse_detected`). Tests: `test_refresh_rotation_and_reuse_detection`,
  `test_logout_revokes_the_session`, `test_password_change_invalidates_existing_tokens`.
* CSRF: the cookie is only sent to `/auth/refresh` and `/auth/logout`, which also require
  `X-Requested-With: flowforge`. A cross-site request with that header needs a CORS preflight, which fails for
  origins not in `FLOWFORGE_CORS_ORIGINS`. Every other endpoint authenticates with a bearer header, not cookies.

### Authorization and tenant isolation
* Roles (owner, admin, developer, viewer) map to permissions in one table (`flowforge_shared/rbac.py`), enforced by
  a dependency on every route. The dashboard hides controls a role cannot use, but never relies on that.
* Every tenant-owned query filters by the project derived from the credentials. Another tenant's resource answers
  404, the same as a missing one (`test_cross_tenant_access_is_indistinguishable_from_not_found`).
* Escalation is blocked: admins cannot grant or touch the owner role, the last owner cannot be removed or demoted,
  and API keys cannot hold administrative scopes (`test_admin_cannot_escalate_to_owner_or_touch_owners`,
  `test_last_owner_cannot_be_removed_or_demoted`, `test_admin_only_scopes_cannot_be_granted_to_keys`).
* Shared infrastructure (draining workers) requires a platform admin (`test_non_admin_cannot_drain_workers`).

### API keys
* Format `ffk_<prefix>_<secret>`. Only a SHA-256 hash and the prefix are stored, and the secret is shown once. Keys
  are bound to one project, carry scopes, can expire, rotate with a grace period, and are revoked immediately
  (`test_api_key_scopes_are_enforced`, `test_revoked_and_expired_keys_stop_working`,
  `test_rotation_with_and_without_grace_period`, and the E2E `api-keys.spec.ts`).

### Input handling
* Request bodies over 1 MiB are rejected before parsing; task payloads over 256 KiB are rejected.
* Every payload is validated against its task type's JSON Schema (generated from the handler's Pydantic model)
  before it is stored. Workers validate again before calling the handler.
* **No arbitrary code execution.** Tasks name a registered handler; nothing evaluates user-supplied code. The shipped
  code contains no `eval`, `exec`, `pickle` of user data, `subprocess` or `shell=True` (checked with `grep`). The
  CPU process pool passes JSON-validated payloads to registered functions.
* SQL uses bound parameters throughout (SQLAlchemy). The few `text()` statements take parameters, never string
  interpolation of user input (ruff `S608` is enabled).
* Queue names, cron expressions, time zones, node keys and identifiers are validated by pattern.

### Abuse protection
* Per-IP limits on unauthenticated auth endpoints and per-identity limits on the API, enforced by a Redis
  sliding-window counter in Lua. Responses are 429 with `Retry-After` (`test_rate_limit_returns_429_with_retry_after`).
* `X-Forwarded-For` is honoured only for the configured number of trusted proxy hops, so it cannot be spoofed
  to dodge limits.

### Transport and browser
* API responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy`, `Content-Security-Policy: default-src 'none'` (except the API docs), and HSTS when
  configured (`test_security_headers_and_cors`).
* Dashboard behind nginx: a same-origin CSP (with the inline-script limitation in [known issues](known-issues.md)),
  frame denial and a permissions policy. `/metrics` is not reachable through the public entry point.
* The production build omits `X-Powered-By`.

### Secrets and logging
* No secrets in source control or images. `.env` is git-ignored and generated with random values
  (`devstack.py init-env`); Compose refuses to start without `POSTGRES_PASSWORD` and `GRAFANA_ADMIN_PASSWORD`.
* Structured logs redact sensitive keys (`password`, `token`, `secret`, `authorization`, `api_key`, `cookie`,
  `credential`) and mask API-key and JWT-shaped strings inside messages. The audit log stores details through the
  same redaction (`test_sensitive_actions_are_audited_without_secrets`).
* The admin CLI reads passwords from an environment variable or a prompt, never from arguments.

### Auditing
Security-relevant actions are written to `audit_logs` in the same transaction as the change: registration, login,
lockout, password change and reset, refresh-token reuse, member and role changes, project and organization changes,
API-key lifecycle, platform-admin grants, workflow and schedule changes, task retry, cancel and archive, and worker
drain. Each entry records actor, IP, user agent and request id. Admins read them per organization in Settings.

## Residual risks

| Risk | Severity | Mitigation |
|---|---|---|
| CSP allows inline scripts | low | Same-origin `connect-src`, `object-src 'none'`, no user HTML is rendered (React escapes output) |
| A handler written by an operator could be unsafe | depends on the operator | Handlers are deployed code reviewed like any other; the platform never executes payloads |
| Single JWT signing key (HS256) | low | Rotating it signs everyone out; asymmetric keys would allow verification-only services, but none exist |
| Rate limiting depends on Redis | low | If Redis is down the limiter fails open for availability and logs a warning; authentication itself does not depend on Redis |
