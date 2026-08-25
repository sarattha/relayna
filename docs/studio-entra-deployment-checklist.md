# Studio Entra And DevOps Deployment Checklist

This is the production handoff checklist for Relayna Studio Entra sign-in. It
follows the Arcweft handoff pattern by separating Microsoft Entra registration
work from deployment and secret-management work, and by requiring a return
package before rollout.

Studio reuses the Entra application registration used by Relayna Gateway, but
it uses a separate callback URI and a separate RSA certificate. The Entra team
must preserve every existing Gateway redirect, certificate, API exposure,
application role, and managed-identity assignment. Studio does not require a
new application registration, a client secret, a delegated product scope, an
application role, or a managed identity.

## Environment Contract

Resolve every placeholder before either team changes production:

| Item | Required value |
| --- | --- |
| Environment | `<environment>` |
| Entra tenant ID | `<tenant-id>` |
| Shared Gateway application ID | `<application-id>` |
| Studio public origin | `https://<studio-host>` |
| Studio Web callback | `https://<studio-host>/studio/auth/callback` |
| Entra issuer | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| Entra discovery URL | `https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration` |
| Requested OIDC scopes | `openid profile email` |
| Client authentication | `private_key_jwt` with RSA, `PS256`, and `x5t#S256` |
| Session lifetime | `28800` seconds by default |
| Login transaction lifetime | `600` seconds by default |

The frontend and `/studio/*` backend routes must use the same origin. Studio
performs authorization code flow with PKCE S256. The backend exchanges the
code, validates the ID token, and gives the browser only an opaque `HttpOnly`,
`SameSite=Lax`, secure session cookie. Entra tokens and the certificate private
key must never enter browser storage.

Studio logout is local session revocation. It does not call Entra global logout,
so no Studio post-logout redirect URI is required in the registration.

## Entra Team Checklist

Complete the change plan-first against the exact tenant and shared application.
The change record must identify the environment, tenant, application, callback,
and public-certificate fingerprint. Never place private keys, client assertions,
raw tokens, or Kubernetes Secret values in the change record or evidence.

### Shared Application And Tenant

- [ ] Confirm the target tenant ID with DevOps.
- [ ] Confirm the application ID is the existing Relayna Gateway application ID.
- [ ] Confirm the application is single-tenant and has a service principal in
      the same tenant.
- [ ] Use the confidential **Web** platform; do not register Studio as an SPA,
      mobile/desktop client, or public client.
- [ ] Keep “Allow public client flows” disabled.
- [ ] Preserve every existing Gateway redirect URI, public certificate, App ID
      URI, API permission, application role, and role assignment.
- [ ] Do not create a second Studio application registration or a client secret.
- [ ] Record whether Enterprise Application user assignment is required and
      which production users or groups are assigned.

### Callback And Authorization Flow

- [ ] Register the exact Web redirect URI
      `https://<studio-host>/studio/auth/callback`.
- [ ] Preserve the exact scheme, hostname, case, path, and port; do not add a
      wildcard or substitute another host.
- [ ] Do not register the callback under the SPA platform.
- [ ] Keep implicit access-token and ID-token grants disabled; Studio uses
      authorization code flow with PKCE S256.
- [ ] Permit the standard OIDC scopes `openid profile email`.
- [ ] Do not add a delegated Relayna product scope or Microsoft Graph permission
      for Studio sign-in.
- [ ] Preserve tenant-owned Conditional Access, MFA, sign-in risk, and user
      assignment policy.

Studio uses Entra only for authentication. Studio roles (`admin`, `readonly`)
and account states (`pending`, `active`, `blocked`) remain in Studio PostgreSQL;
do not create similarly named Entra roles or groups and assume they grant Studio
access.

### Studio Certificate

- [ ] Receive only the approved Studio public X.509 certificate from DevOps.
- [ ] Register it on the shared application under **Certificates**, without
      removing the Gateway certificate or a currently active Studio certificate.
- [ ] Do not request, receive, email, ticket, upload, or store the private key.
- [ ] Record the certificate SHA-256 fingerprint, portal thumbprint, subject,
      serial number, activation time, and expiry.
- [ ] Confirm the certificate is an RSA certificate and is enabled for the
      intended rollout window.
- [ ] During rotation, keep old and new Studio public certificates registered
      until DevOps verifies that all backend replicas use the new pair.
- [ ] Confirm the token endpoint accepts Studio certificate assertions using
      `private_key_jwt`, `PS256`, and `x5t#S256`.

The assertion uses the shared application ID as `iss` and `sub`, the discovered
token endpoint as `aud`, a unique `jti`, and a five-minute validity window.

### ID-Token Contract

Complete a test authorization-code login and retain a redacted decoded token
header and payload. Never retain or send the raw ID token.

- [ ] `alg` is `RS256`, and `kid` resolves through the discovery document's
      JWKS URI.
- [ ] `iss` exactly matches the handed-off tenant-specific issuer.
- [ ] `aud` is the shared application ID.
- [ ] `tid` is the exact configured tenant ID.
- [ ] `oid` is the immutable object ID of the signed-in user.
- [ ] `sub`, `nonce`, `iat`, `nbf`, and `exp` are present.
- [ ] `email` or `preferred_username` contains a usable email address.
- [ ] The test user's Enterprise Application assignment and Conditional Access
      requirements are satisfied.

Studio validates signature, algorithm, issuer, tenant, audience, nonce, time
claims, subject, object ID, and usable email. Email is display and bootstrap
data; tenant ID plus object ID form the durable member identity.

### Entra Return Package

- [ ] Environment and tenant ID.
- [ ] Application display name, application ID, and application object ID.
- [ ] Enterprise Application display name and service-principal object ID.
- [ ] Exact registered Studio Web callback URI.
- [ ] Confirmation that existing Gateway registration state was preserved.
- [ ] Assignment-required setting and assigned production users/groups.
- [ ] Studio public-certificate fingerprints and validity dates.
- [ ] Exact issuer and discovery URL.
- [ ] Confirmation that no Studio client secret, delegated product scope,
      Microsoft Graph permission, application role, or managed identity was
      added.
- [ ] Redacted decoded ID-token header and payload from a test user.
- [ ] Test timestamp and Entra correlation/request ID for troubleshooting.

Entra sign-off is complete only when this package is returned to DevOps and the
test ID token satisfies the contract above.

## DevOps Team Checklist

DevOps owns the environment-specific hostname and routing, certificate private
key, Kubernetes ConfigMaps and Secrets, PostgreSQL and Redis readiness,
deployment evidence, rotation, and rollback. Entra application mutations remain
with the Entra team. Use normal Kubernetes resources referenced directly by the
Deployment; this handoff does not require or define a Helm chart.

### Identify The Target Before Mutation

- [ ] Record the environment, Azure subscription, cluster, resource group,
      namespace, and kube context, where applicable.
- [ ] Confirm the dedicated HTTPS Studio hostname and exact public origin.
- [ ] Confirm that the frontend and `/studio/*` backend routes share that origin.
- [ ] Give the Entra team the tenant ID, shared application ID, exact callback
      URI, and approved public certificate.
- [ ] Record the current backend and frontend image digests, deployment
      revisions, ConfigMap resource version, Secret backup/version references,
      PostgreSQL backup, and Redis recovery point.
- [ ] Identify the rollout owner, Entra contact, database owner, and rollback
      decision maker.

### ConfigMap And Secret Contract

Create these standard Kubernetes resources in the Studio namespace. Do not put
Secret values in Git. Store the recoverable Secret source in the team's approved
encrypted secret store, then create or update the Kubernetes Secret through the
normal deployment process.

| Resource | Type | Contents |
| --- | --- | --- |
| `relayna-studio-config` | `ConfigMap` | Non-secret Entra identifiers and URLs, certificate mount paths, cookie policy, and TTLs |
| `relayna-studio-runtime-secrets` | `Secret` (`Opaque`) | PostgreSQL URL, Redis URL, and temporary bootstrap administrator allowlists |
| `relayna-studio-entra-oidc` | `Secret` (`Opaque`) | Studio-specific RSA private key and matching public X.509 certificate |

- [ ] Create `relayna-studio-config` from reviewed, environment-specific values.
- [ ] Create `relayna-studio-runtime-secrets` without printing its values or
      committing its rendered manifest.
- [ ] Create `relayna-studio-entra-oidc` from the approved certificate files,
      preserving the exact key names below.
- [ ] Reference the ConfigMap and runtime Secret directly with `envFrom` in the
      backend Deployment.
- [ ] Mount the certificate Secret directly as a read-only volume in the
      backend Deployment.
- [ ] Give the migration Job access to
      `RELAYNA_STUDIO_DATABASE_URL` from `relayna-studio-runtime-secrets`.
- [ ] Do not template these resources through Helm and do not duplicate their
      values in Deployment environment entries.

The normal resource shapes are:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: relayna-studio-config
data:
  RELAYNA_STUDIO_ENTRA_APPLICATION_ID: "<shared-application-id>"
  RELAYNA_STUDIO_ENTRA_TENANT_ID: "<tenant-id>"
  RELAYNA_STUDIO_ENTRA_ISSUER: "https://login.microsoftonline.com/<tenant-id>/v2.0"
  RELAYNA_STUDIO_ENTRA_OIDC_DISCOVERY_URL: "https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration"
  RELAYNA_STUDIO_ENTRA_OIDC_REDIRECT_URI: "https://<studio-host>/studio/auth/callback"
  RELAYNA_STUDIO_ENTRA_OIDC_PRIVATE_KEY_PATH: "/run/secrets/relayna-studio-entra/studio-private-key.pem"
  RELAYNA_STUDIO_ENTRA_OIDC_CERTIFICATE_PATH: "/run/secrets/relayna-studio-entra/studio-certificate.pem"
  RELAYNA_STUDIO_SESSION_TTL_SECONDS: "28800"
  RELAYNA_STUDIO_LOGIN_TTL_SECONDS: "600"
  RELAYNA_STUDIO_SESSION_COOKIE_SECURE: "true"
---
apiVersion: v1
kind: Secret
metadata:
  name: relayna-studio-runtime-secrets
type: Opaque
stringData:
  RELAYNA_STUDIO_DATABASE_URL: "<managed-outside-git>"
  RELAYNA_STUDIO_REDIS_URL: "<managed-outside-git>"
  RELAYNA_STUDIO_ENTRA_ADMIN_EMAILS: "<temporary-bootstrap-admin-emails>"
  RELAYNA_STUDIO_ENTRA_ADMIN_OBJECT_IDS: "<temporary-bootstrap-admin-object-ids>"
---
apiVersion: v1
kind: Secret
metadata:
  name: relayna-studio-entra-oidc
type: Opaque
data:
  studio-private-key.pem: "<base64-managed-outside-git>"
  studio-certificate.pem: "<base64-managed-outside-git>"
```

The placeholders document the contract only; do not apply or commit this
example with real or placeholder Secret values. The backend Deployment consumes
the resources directly:

```yaml
envFrom:
  - configMapRef:
      name: relayna-studio-config
  - secretRef:
      name: relayna-studio-runtime-secrets
volumeMounts:
  - name: relayna-studio-entra
    mountPath: /run/secrets/relayna-studio-entra
    readOnly: true
volumes:
  - name: relayna-studio-entra
    secret:
      secretName: relayna-studio-entra-oidc
```

### Certificate Handling

- [ ] Generate or obtain a Studio-specific RSA X.509 key pair through the
      approved Key Vault, HSM, or PKI process.
- [ ] Use an unencrypted PEM private key readable by the backend and a matching
      PEM X.509 certificate.
- [ ] Verify that the private key and certificate contain the same RSA public
      key before creating `relayna-studio-entra-oidc`.
- [ ] Send only the public certificate and fingerprints to the Entra team.
- [ ] Store the recoverable private key and certificate in the approved encrypted
      secret store; do not commit them or their Kubernetes Secret manifest.
- [ ] Mount both files read-only into the Studio backend and into no frontend or
      unrelated workload.
- [ ] Set `RELAYNA_STUDIO_ENTRA_OIDC_PRIVATE_KEY_PATH` and
      `RELAYNA_STUDIO_ENTRA_OIDC_CERTIFICATE_PATH` to those mounted files.
- [ ] Keep the previous encrypted Secret source recoverable for the rollback
      window and record the applied Kubernetes Secret resource version.
- [ ] Record certificate owner, activation, expiry, renewal lead time, alert,
      and emergency rotation procedure.
- [ ] Ensure pipelines never print private keys, database/Redis URLs, cookies,
      client assertions, authorization codes, or provider tokens.

Studio loads and compares the certificate and private key during startup. A
non-RSA key, invalid certificate, encrypted private key, or mismatched pair
prevents startup.

### Runtime Configuration

- [ ] Set `RELAYNA_STUDIO_ENTRA_APPLICATION_ID` to the handed-off shared
      application ID.
- [ ] Set `RELAYNA_STUDIO_ENTRA_TENANT_ID` to the handed-off tenant ID.
- [ ] Set `RELAYNA_STUDIO_ENTRA_ISSUER` and
      `RELAYNA_STUDIO_ENTRA_OIDC_DISCOVERY_URL` to the exact Entra return values.
- [ ] Set `RELAYNA_STUDIO_ENTRA_OIDC_REDIRECT_URI` to the exact registered HTTPS
      callback.
- [ ] Keep `RELAYNA_STUDIO_SESSION_COOKIE_SECURE=true` in production.
- [ ] Review the default eight-hour session TTL and ten-minute login TTL against
      the environment's policy before changing them.
- [ ] Configure `RELAYNA_STUDIO_DATABASE_URL` for PostgreSQL and
      `RELAYNA_STUDIO_REDIS_URL` for Redis without exposing either value.
- [ ] Budget the PostgreSQL pool across all replicas and confirm the exact
      Alembic revision is deployed.
- [ ] Keep all frontend-to-backend browser calls on the same origin; do not add
      a frontend Entra token flow or authentication bypass.

### Initial Administrator

- [ ] Select at least one controlled bootstrap administrator.
- [ ] Obtain both the normalized email and immutable Entra object ID for every
      intended bootstrap administrator.
- [ ] Configure both bootstrap allowlists before starting against a fresh
      PostgreSQL database. Do not set only one list.
- [ ] Verify the intended user signs in as an active Studio administrator.
- [ ] Verify the administrator can activate a separate pending user and assign
      the minimum required Studio role.
- [ ] After at least one active administrator is durably stored, remove both
      bootstrap keys from `relayna-studio-runtime-secrets` if the operational
      policy permits, roll the backend, and verify administrator access again.
- [ ] Do not use an Entra role or group claim as a substitute for Studio's
      database-backed administrator state.

### Platform And Network Readiness

- [ ] Run the required Alembic migration job before starting the new backend;
      the application does not migrate its own database.
- [ ] Confirm `/readyz` passes PostgreSQL connectivity, exact schema revision,
      and Redis connectivity before routing traffic.
- [ ] Confirm `/livez` and `/healthz` are used only as liveness signals, not as
      deployment-readiness gates.
- [ ] Permit the Studio backend to resolve and reach the handed-off Entra
      discovery, token, and JWKS endpoints over HTTPS.
- [ ] Ensure node and pod clocks are synchronized because nonce, token, and
      client-assertion validation are time-bound.
- [ ] Keep the Studio origin behind approved internal ingress, VPN/IAP, source
      ranges, and TLS policy.
- [ ] Keep PostgreSQL, Redis, Gateway/service registration endpoints, and
      observability endpoints within their approved private network boundary.
- [ ] Confirm the frontend image proxies `/studio/*` to the backend and does not
      cache authentication responses.

### Pre-Deployment Verification

- [ ] The Entra return package is complete and matches the rendered runtime
      configuration.
- [ ] The callback in Entra, ingress, and
      `RELAYNA_STUDIO_ENTRA_OIDC_REDIRECT_URI` is byte-for-byte identical.
- [ ] Certificate and private-key public hashes match.
- [ ] Certificate fingerprint and validity match the Entra return package.
- [ ] Expected Secret keys and mount paths exist without displaying values.
- [ ] The backend Deployment resolves both `envFrom` references and mounts
      `relayna-studio-entra-oidc` read-only at the configured path.
- [ ] Backend and frontend images are immutable and digest-pinned to one
      reviewed source revision.
- [ ] Database migration and `/readyz` checks pass in the target environment.
- [ ] PostgreSQL and Redis recovery points and the previous deployment revision
      are recorded.
- [ ] Logs and rendered manifests contain no private key, token, authorization
      code, cookie, or credential value.

### Post-Deployment Acceptance

- [ ] Start sign-in from the production Studio origin and complete Entra MFA or
      Conditional Access requirements.
- [ ] Verify state, nonce, PKCE S256, and certificate-authenticated code exchange
      succeed.
- [ ] Verify the browser receives an opaque secure `HttpOnly` session cookie and
      never receives an Entra token.
- [ ] Verify the bootstrap administrator is active and can administer Access.
- [ ] Verify a first-time ordinary assigned user is `pending` and cannot read
      Studio data until activated.
- [ ] Verify an activated `readonly` user can read but cannot mutate Studio
      resources.
- [ ] Verify blocked, wrong-tenant, wrong-audience, expired, missing-nonce, and
      revoked-session cases fail closed.
- [ ] Verify unsafe cookie-authenticated requests fail without the current
      `X-CSRF-Token`.
- [ ] Verify local logout revokes the Studio session and returns to the login
      screen without relying on Entra global logout.
- [ ] Confirm provider tokens, authorization codes, private keys, and session
      cookie values do not appear in browser storage, PostgreSQL, logs, traces,
      or operational evidence.
- [ ] Record the test time, deployment revision, non-secret correlation IDs,
      and pass/fail owner.

### Certificate Rotation And Rollback

- [ ] Register the new public certificate in Entra before deploying its private
      key and certificate to Studio.
- [ ] Keep the old public certificate registered and the previous encrypted
      Secret source recoverable during the observation window.
- [ ] Roll all backend replicas and verify sign-in on the new certificate.
- [ ] Remove the old public certificate only after old replicas are gone and
      acceptance checks pass.
- [ ] If authentication fails, stop new access, restore the recorded backend and
      frontend revisions plus the previous ConfigMap and Secret contents, and verify
      sign-in and one representative Studio read before reopening access.
- [ ] Follow the maintenance-window and data-loss guidance in
      [Studio persistence](studio-persistence.md) when rollback crosses the
      PostgreSQL cutover boundary; do not use Alembic downgrade as a shortcut.

## Go/No-Go Gate

Deployment is **No-Go** if the tenant, application ID, issuer, discovery URL, or
callback differs between the Entra return package and the rendered deployment;
the Studio certificate is unregistered, expired, non-RSA, or mismatched; secure
cookies are disabled for production HTTPS; bootstrap administration is not
prepared; PostgreSQL migration or `/readyz` fails; the rollback point is absent;
or existing Gateway registration state would be removed or replaced.

Deployment is **Go** only when both teams have signed off, the return package is
complete, pre-deployment verification passes, an immutable rollback point is
recorded, and the rollout owner is ready to execute the post-deployment
acceptance checks.

## Ownership Summary

| Responsibility | Owner |
| --- | --- |
| Shared application, Studio Web callback, public-certificate registration, Enterprise Application assignment | Entra team |
| Existing Gateway redirects, API exposure, app roles, certificates, and assignments | Gateway and Entra owners; Studio rollout must preserve them |
| Private key, Kubernetes ConfigMap/Secrets, ingress, database migration, Redis, deployment, rotation, rollback | DevOps team |
| Bootstrap administrator identities and approved production users/groups | Product/security owner with Entra and DevOps |
| Studio member status and `admin`/`readonly` authorization after login | Relayna Studio PostgreSQL and Studio administrators |
| Authentication implementation and acceptance behavior | Relayna engineering |

See [Studio Entra authentication](studio-entra-auth.md) for the runtime security
model and [Studio persistence](studio-persistence.md) for migration, backup, and
rollback requirements.
