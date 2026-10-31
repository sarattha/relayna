# Reusable observability profiles

Studio administrators can save Loki, Prometheus, and Tempo connections once and
select them when registering or editing services. Open **Services → Observability
Profiles → Manage observability profiles** to create, edit, test, or delete a
connection.

## Create and use a profile

Enter a name, environment, provider, and endpoint reachable from the Studio
backend. Base paths are supported, such as `https://metrics.example.com/prometheus`.
The endpoint must satisfy Studio's existing outbound host/network allowlist.
URLs with embedded credentials, query parameters, or fragments are rejected.
Loki and Tempo support an optional tenant ID sent as `X-Scope-OrgID`.

Choose no authentication, a bearer token, or basic username/password. Credentials
are encrypted using `RELAYNA_STUDIO_SETTINGS_ENCRYPTION_KEY`, the same Fernet key
used for other encrypted Studio connection settings. Configure this key through
your deployment's secret mechanism before saving authenticated profiles. All
replicas must use the same key. PostgreSQL is required for profile storage;
unauthenticated profiles do not require an encryption key.

Optional provider defaults are a JSON object containing query settings, such as:

```json
{"pod_label": "pod", "source_label": "app", "task_match_mode": "contains"}
```

For Prometheus, defaults can include `namespace`, `namespace_label`, `pod_label`,
`container_label`, `step_seconds`, and `task_window_padding_seconds`. Tempo
supports `public_base_url` and `query_path`. Service selector values and runtime
service-label values are excluded from profiles because they identify individual
services.

In the service registration editor, expand Log, Metrics, or Trace Configuration
and choose a saved profile. Only profiles matching that provider and the service's
exact environment are offered. The endpoint and tenant become inherited values;
query defaults are copied into editable service fields. Enter the service's own
selectors and, for Prometheus, its namespace. Save the service normally.

Changing a draft's environment clears profile bindings and their inherited
connection values. Choose profiles for the new environment or configure manual
connections before saving. Existing manual configurations remain supported.
Choosing **Manual configuration** detaches a profile while retaining the current
non-secret configuration; credentials are never copied into the service.

## Test, edit, and rotate

**Test** checks the saved connection through a bounded read request: Loki labels,
Prometheus `vector(1)`, or Tempo search tags. It distinguishes connection failures,
rejected credentials/permissions, and unexpected provider responses. Redirects
are not followed. This verifies connectivity; service selectors still need to be
correct for telemetry queries to return service data.

Endpoint, tenant, and credential changes apply to every linked service on its
next request, without restarting Studio. Service-owned query settings and
selectors remain unchanged when profile defaults are edited; the defaults apply
when selecting a profile in the editor.

Credential fields are always blank on edit. Leave them blank to keep existing
credentials, or supply a complete replacement. When changing endpoint or tenant,
re-enter the credentials to avoid sending an old credential to a new destination.
Choose authentication **None** to explicitly clear stored credentials. Provider
and environment are immutable; create another profile to change them. Stale
updates are rejected; reload before retrying rather than overwriting another
administrator's change.

The manager lists services using each profile. Deletion is blocked while a
non-deleted service references it, including concurrent registration attempts.
Detach all services before deleting a profile. Read-only members cannot access
the manager or its admin API. Browser mutations use the existing CSRF protection.

## Storage and recovery

Versioned profile documents live under `observability-profile:v1:<id>` in
`studio_operator_settings`; credentials are ciphertext, and API responses expose
only whether a credential is configured. Profile references are optional
`profile_id` fields in existing service provider-configuration JSON. No SQL
schema migration or existing-service backfill is needed. Mutations are audited
without credentials; binding changes and profile deletion checks are serialized
in PostgreSQL transactions.

Back up the settings encryption key separately from database backups. Losing or
replacing it makes stored credentials unavailable. Restore the original key or
replace credentials through the profile editor. Failed resolution/decryption
never falls back to an unauthenticated request. Inline configurations remain
independent of the key.

Before rolling back to a backend without profile support, detach profile-bound
services into suitable inline configurations and arrange upstream authentication
compatible with that backend. Older backends cannot honor stored profile
references or profile credentials. Do not use a mixed-version rollout that would
send bound services through older backends.

The administrator API is rooted at `/studio/admin/observability-profiles`: GET
lists profiles, POST creates one, GET/PUT/DELETE `/{id}` reads/updates/deletes,
and POST `/{id}/test` tests the saved connection. Updates require `revision` in
the complete profile body; deletion requires `?revision=<current revision>`.
Responses never return credentials or ciphertext.
