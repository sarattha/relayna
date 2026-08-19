# Studio Entra Authentication

Relayna Studio 1.6.0 requires Microsoft Entra authentication for every human
user. Authentication is implemented as a backend-for-frontend flow: the Studio
backend performs the authorization-code exchange and the browser receives only
an opaque Studio session cookie. Entra tokens never enter frontend storage.

## Roles And Account States

Studio resolves the current member record on every request, so role and status
changes take effect immediately.

| Value | Access |
| --- | --- |
| `admin` | All reads, mutations, and Access administration. |
| `readonly` | Human-facing GET, HEAD, and SSE APIs only. Mutation controls and Access navigation are hidden. |
| `pending` | Session and local logout only; an administrator must activate the member. |
| `active` | Access according to the member role. |
| `blocked` | Session and local logout only; Studio data is denied. |

A first-time identity that is not a bootstrap administrator is stored as
`pending` and `readonly`. Members are keyed by the verified Entra tenant ID and
immutable `oid`; email is display/contact data and is normalized from `email`
or `preferred_username`.

## Entra Application And Certificate

Studio may reuse the Entra application registration already used by Relayna
Gateway. Add the Studio callback URI to that registration, but create a
separate RSA certificate/private key for Studio. Register only the Studio
public certificate with Entra and mount the private key only in the Studio
backend workload. Never copy or mount the Gateway private key into Studio.

For production, use a dedicated Studio hostname on the existing internal
ingress, for example:

```text
https://studio.internal.example/studio/auth/callback
```

The browser-facing frontend and `/studio/*` backend routes must share that
origin. Keep the default secure cookie setting enabled behind HTTPS.

## Required Configuration

| Variable | Purpose |
| --- | --- |
| `RELAYNA_STUDIO_ENTRA_APPLICATION_ID` | Shared Entra application/client ID. |
| `RELAYNA_STUDIO_ENTRA_TENANT_ID` | Allowed tenant ID; must match the ID token `tid`. |
| `RELAYNA_STUDIO_ENTRA_ISSUER` | Exact accepted ID-token issuer. |
| `RELAYNA_STUDIO_ENTRA_OIDC_DISCOVERY_URL` | OIDC discovery document URL. |
| `RELAYNA_STUDIO_ENTRA_OIDC_REDIRECT_URI` | Registered Studio callback URI. |
| `RELAYNA_STUDIO_ENTRA_OIDC_PRIVATE_KEY_PATH` | Studio-only RSA private-key PEM path. |
| `RELAYNA_STUDIO_ENTRA_OIDC_CERTIFICATE_PATH` | Matching Studio X.509 certificate PEM path. |
| `RELAYNA_STUDIO_ENTRA_ADMIN_EMAILS` | Comma-separated bootstrap administrator emails. |
| `RELAYNA_STUDIO_ENTRA_ADMIN_OBJECT_IDS` | Comma-separated bootstrap Entra object IDs. |

Bootstrap email and object-ID allowlists must either both be set or both be
absent. A bootstrap identity is accepted only when tenant, normalized email,
and object ID all match. On a fresh PostgreSQL database, startup fails unless the
allowlists are present or an active administrator already exists. After the
first administrator is persisted, remove both bootstrap lists if desired.
Bootstrap configuration never reactivates a blocked member.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RELAYNA_STUDIO_SESSION_TTL_SECONDS` | `28800` | Fixed, non-sliding Studio session lifetime. |
| `RELAYNA_STUDIO_LOGIN_TTL_SECONDS` | `600` | Single-use login transaction lifetime. |
| `RELAYNA_STUDIO_SESSION_COOKIE_SECURE` | `true` | Adds the cookie `Secure` attribute; set `false` only for local HTTP. |

See `.env.studio.example` at the repository root for a complete deployment
template.

## Route Policy

The following routes intentionally remain outside browser sessions:

- `GET /studio/gateway/services`
- `POST /studio/ingest/events`
- `/metrics`
- `/healthz`, `/readyz`, and `/livez`

Login, callback, and non-secret auth configuration are public. Session
inspection and local logout remain available to pending and blocked members.
All other Studio and OpenAPI backend routes are protected by default. Every
unsafe cookie-authenticated request, including logout, must send the current
session's `X-CSRF-Token` value.

Local logout revokes only the Studio session and returns to the Studio login
screen. It deliberately does not invoke Entra global logout.

## Local Development With The Gateway Issuer

Use the committed development issuer in the sibling `relayna-gateway` checkout;
do not add a frontend auth bypass. The issuer has selectable admin and pending
identities and exercises the real Studio callback, token validation, member,
session, and CSRF paths.

First generate a short-lived Studio certificate in a temporary directory:

```bash
cd ../relayna-gateway
./scripts/entra/generate-development-portal-certificate.sh \
  --output-dir /tmp/relayna-studio-oidc
```

Then start the Gateway issuer for Studio's Vite origin:

```bash
RELAYNA_ENV=development \
RELAYNA_DEV_OIDC_PORT=18090 \
RELAYNA_DEV_OIDC_ISSUER=http://127.0.0.1:18090 \
RELAYNA_DEV_OIDC_BROWSER_CERTIFICATE_PATH=/tmp/relayna-studio-oidc/portal-certificate.pem \
RELAYNA_DEV_OIDC_BROWSER_REDIRECT_URI=http://127.0.0.1:5173/studio/auth/callback \
node scripts/entra/development-oidc.mjs
```

From the Relayna repository, run Redis, the Studio backend with the local
values in `.env.studio.example`, and the Vite frontend. The issuer's
`Gateway Administrator` persona is the bootstrap administrator
(`gateway.admin@relayna.dev`, object ID ending in `0002`); `Pending Service
Owner` exercises approval and readonly assignment. Local HTTP requires
`RELAYNA_STUDIO_SESSION_COOKIE_SECURE=false`.

## Operational Security Notes

- Redis stores only SHA-256 hashes of random 256-bit session and login tokens;
  durable member/RBAC records are stored transactionally in PostgreSQL.
- Sessions expire after a fixed eight hours by default and are not refreshed by
  activity.
- Discovery and JWKS are cached, while ID tokens are validated for signature,
  algorithm, issuer, tenant, audience, nonce, and time claims.
- The token endpoint authenticates Studio using a PS256 `private_key_jwt` with
  the certificate's `x5t#S256` thumbprint.
- Self-blocking, self-demotion, and changes that would leave zero active
  administrators are rejected under a PostgreSQL transaction lock.

Certificate-secret installation and Entra application changes are operator
actions. Rolling back from 1.6.0 requires the maintenance-window procedure in
[Studio persistence](studio-persistence.md); old versions cannot read members
or other durable Studio writes from PostgreSQL.
