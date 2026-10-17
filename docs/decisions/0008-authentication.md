# 0008. Short JWTs, rotating refresh cookies with reuse detection, hashed API keys

**Status:** Accepted

## Context

The dashboard needs browser sessions that resist token theft. Services and the SDK need non-interactive credentials
that can be scoped, rotated and revoked independently.

## Decision

* **People.** A 15-minute HS256 access token kept only in memory, plus a refresh token in an `httpOnly`, `SameSite`
  cookie scoped to the auth path. Every refresh rotates the token, and reusing an already-rotated token revokes that
  whole token family (theft detection). Passwords are hashed with Argon2id, and repeated failed logins lock the
  account temporarily.
* **Services.** Project-scoped API keys (`ffk_…`) are stored as SHA-256 hashes with a clear-text prefix for
  identification. A key carries a subset of the non-administrative scopes, can expire, and rotates with a
  configurable grace period.
* **Authorization.** Roles (owner, admin, developer, viewer) per organization, enforced on every endpoint. Resources
  in other tenants answer 404.

## Consequences

* A full page load costs one refresh round trip, because the access token is not persisted. The auth endpoints are
  rate limited per IP.
* Keys can never manage members, other keys or the organization.
