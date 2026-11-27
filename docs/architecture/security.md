# Security model

Status: Phase 6. Implementation: `services/auth/`, `apps/api/chainsight_api/auth.py`,
`apps/api/chainsight_api/routers/{auth,org,webhooks}.py`, `services/audit.py`.

## Identities and credentials

| Credential | Used by | Stored as | Lifetime |
|---|---|---|---|
| Password | People, at `/login` | Argon2id (argon2-cffi defaults: RFC 9106 low-memory profile, 64 MiB, t=3, p=4); rehashed on login when parameters change | Until changed |
| Access token (JWT, HS256) | The web app (httpOnly cookie `cs_access`) or `Authorization: Bearer` | Not stored; signed with `JWT_SECRET`; claims `sub`, `org`, `sid`, `role`, `iss`, `aud`, `typ=access` | 15 min (`JWT_ACCESS_TTL_MINUTES`) |
| Refresh token (opaque) | Cookie `cs_refresh` (path `/api/v1/auth`) | SHA-256 in `auth_sessions.refresh_hash`; **rotated on every use** | 14 days (`AUTH_REFRESH_TTL_DAYS`) |
| API key `cs_<prefix>_<secret>` | Scripts and integrations: `X-API-Key` or `Authorization: Bearer` | SHA-256 + 8-character lookup prefix; plaintext shown once | Default 90 days, or never |
| Webhook secret | Carriers pushing events | `CARRIER_WEBHOOK_SECRETS` (environment) | Rotate by listing old and new |

Every access token is checked against its `auth_sessions` row and the caller's membership on
**each request**. Signing out, deactivating a member or removing them takes effect at once,
not when the token expires. Roles are re-read at refresh.

Sign-in failures return a single generic message whether the email is unknown, the password
is wrong or the account is inactive. Unknown emails still run an Argon2 verification, so
response times do not reveal which accounts exist.

Password policy: at least 12 characters, at least 5 distinct characters, and not the email
address. Long passphrases are encouraged; there are no composition rules.

## Browser sessions and CSRF

- Cookies are `HttpOnly` and `SameSite=Strict`, and `Secure` in production (enforced). Tokens
  never reach JavaScript; the login response contains no token.
- Cookie-authenticated `POST/PUT/PATCH/DELETE` requests must carry
  `X-Requested-With: chainsight`. A cross-site page cannot add that header without a CORS
  preflight, which the API refuses for origins outside `API_CORS_ORIGINS`. This is a second
  layer on top of SameSite. API-key and bearer requests are exempt, because a browser never
  attaches them automatically.
- After sign-in the app only redirects to same-site relative paths (`safeNext`): there are no
  open redirects.
- The WebSocket handshake is authenticated with the same cookie (or a key or bearer token)
  and also checks `Origin`.

## Authorization (RBAC)

Roles nest: each one includes all permissions of the roles below it.

| Permission | viewer | analyst | admin | owner |
|---|:-:|:-:|:-:|:-:|
| `data:read`: fleet, ports, predictions, research, the org's shipments and experiments | ✓ | ✓ | ✓ | ✓ |
| `shipments:write`: track, refresh, CSV import | | ✓ | ✓ | ✓ |
| `research:write`: create and run experiments | | ✓ | ✓ | ✓ |
| `members:manage`, `api_keys:manage`, `integrations:manage`, `audit:read` | | | ✓ | ✓ |
| `org:manage`: rename the org; grant or revoke the owner role | | | | ✓ |

Every router except health, `/auth/*` and the HMAC-signed webhook requires `data:read`.
Write endpoints require their own permission. Rules enforced on top of that:

- Admins can assign roles up to admin. Only owners can create, demote or remove owners.
- The last active owner cannot be demoted, deactivated or removed.
- Members cannot change their own role or status.
- An API key gets a role's permissions, never more than its creator has, and never `owner`.
  Unknown scopes are ignored (fail closed).

## Organization isolation

Org-owned data (`shipments`, `containers`, `research_experiments`, `api_keys`,
`audit_logs`, `auth_sessions`) is always filtered by the **caller's** organization, taken
from the verified principal and never from the request. IDs belonging to another
organization return 404, not 403, so their existence is not revealed. Integration tests check
that a second organization cannot read, refresh, list or derive predictions from the first
one's shipments, experiments or members.

Shared by design: AIS positions, vessels, ports, port congestion, model registry and public
research series (PortWatch, FRED). These are public-source data and look the same to every
tenant. Platform alerts (feed freshness, vessel anomalies) are also global.

## Rate limiting

Fixed one-minute windows in Redis (`rl:<bucket>:<window>`):

| Bucket | Default |
|---|---|
| Per client IP, every `/api/v1` request except health | 600/min |
| Per authenticated principal (user or API key) | 600/min |
| Sign-in per email (hashed) and per IP | 10/min |

Responses are `429` with `Retry-After`. If Redis is unreachable the limiter **fails open**
and logs `rate_limit_unavailable`, so a cache outage cannot take the API down. The
`ChainSightTargetDown` alert covers Redis indirectly through readiness.

**Client IP:** `X-Forwarded-For` is honoured only when `TRUSTED_PROXY_COUNT` > 0, and then
only the entry that many hops from the right. Next.js forwards a client-supplied header
unchanged, so it does not count as a trusted hop. The production deployment puts Caddy in
front: Caddy replaces the header with the real peer address, so 1 is correct there. Values
that are not IP addresses are discarded rather than stored.

## Carrier webhooks

`POST /api/v1/webhooks/carriers/{code}` with
`X-ChainSight-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>` and
`X-ChainSight-Delivery: <id>`:

- The timestamp must be within ±5 minutes (`WEBHOOK_TOLERANCE_SECONDS`). Signatures are
  compared in constant time, and several `v1=` values are allowed during secret rotation.
- `(carrier, delivery id)` is unique in `webhook_deliveries`, so a replay is acknowledged as a
  duplicate and ignored.
- Events only update shipments that some organization already tracks with that carrier. A
  push can never create shipments.
- Carriers without a configured secret get 404, whether or not the carrier exists.
- This is ChainSight's own scheme. A carrier that signs differently (for example DCSA's
  `Notification-Signature`) needs a small relay or an adapter-specific verifier.

## Audit trail

`audit_logs` is **append-only**: a trigger rejects `DELETE`, and any `UPDATE` other than
`ON DELETE SET NULL` clearing an actor or organization reference. This holds even for the
application's database user, and survives backup and restore (verified in the restore drill).

Recorded events: sign-ins and failures (failures store a hash of the email, not the address),
sign-outs, password changes, members added, updated or removed, API keys created or
revoked, organization renames, shipments tracked or imported, experiments created or run,
carrier webhook deliveries, and CLI-created users. Admins see the log in Settings.

Secrets and passwords are never written to `changes`.

## Secrets and configuration

- All secrets are `SecretStr` settings from the environment. They are masked in reprs and
  dumps, and never logged or returned.
- `APP_ENV=production` refuses to start without a `JWT_SECRET` of at least 32 characters,
  with a default database password, with `AUTH_COOKIE_SECURE=false`, or with a non-HTTPS
  CORS origin.
- In development an unset `JWT_SECRET` gives a random per-process secret (with a warning):
  sessions end when the API restarts.
- Rotating `JWT_SECRET` only expires access tokens; refresh cookies would keep issuing new
  ones. After a suspected leak, also run `python -m services.auth revoke-sessions --all` (or
  `--email`). It revokes every refresh session at once and audits it in each affected org.
- `/metrics` is served outside `/api/v1`, so neither the web proxy nor Caddy exposes it. Set
  `METRICS_TOKEN` if the API port is reachable by anyone other than Prometheus.

## Known limitations

- No email delivery: no self-service password reset or email invitations. Admins create
  members with an initial password and can reset it (`python -m services.auth set-password`).
- No MFA, SSO (OIDC or SAML) or account lockout. Sign-in throttling is per minute.
- One organization per session: a user in several organizations chooses one at sign-in
  (`organization` slug) and signs in again to switch.
- Refresh-token reuse detection only rejects the replayed token. It does not revoke the
  whole session family.
- Rate limits are approximate (fixed windows allow up to 2× the limit at a window boundary).
