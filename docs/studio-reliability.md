# Reliability goals and alert routing

Studio's **Reliability** workspace defines owner/service/environment goals, previews
current evidence, exports Prometheus rules and Alertmanager routing snippets, and
shows delivery receipts with scoped investigation links. Saving a goal does **not**
install rules. Use your existing deployment workflow to merge and reload exports.
Prometheus evaluates rules continuously; Alertmanager owns grouping, silences,
inhibition and notification routing. Studio is an optional bridge to the same HTTP
email transport used by failed-task notifications.

## Setup and permissions

Goal persistence requires PostgreSQL with the existing schema. No new migration is
needed: versioned `reliability:v1:<id>` documents use `studio_operator_settings`.
Administrators can create/delete goals; active read-only members can inspect goals,
preview evidence and export configuration. Changes and deliveries are audited.
Goals are immutable; create a replacement and retire the old rules to change one.

Register a service with its real environment and Prometheus `metrics_config`.
Choose its owner, email recipients, signal/objective, threshold, window, freshness,
and hold duration. Environment must match the enabled service registry record.
Thresholds are inclusive for early signals (`>=`) and exclusive for objective
breaches (`good / eligible * 100 < target`). The completion deadline only changes
business cohort semantics; early signals use their threshold/window as below.

For SDK runtime signals, explicitly confirm that the datasource and runtime
`service` selector isolate this environment. SDK metrics have no environment label.
Use separate datasources or distinct runtime service labels for environments; do
not acknowledge this confirmation for a shared selector that combines environments.

Configure `RELAYNA_STUDIO_RELIABILITY_WEBHOOK_TOKEN` with a dedicated random secret
and restart Studio. Set `RELAYNA_STUDIO_RELIABILITY_PUBLIC_URL` to the public
HTTP(S) Studio origin (without a path or credentials) for clickable email and alert
links; otherwise messages contain relative Studio paths. Configure the existing failed-task email URL, API key and default
receivers, and enable shared email notifications in **Failed Tasks**. The defaults
remain necessary to configure that existing transport; reliability deliveries use
only the recipients on their own goal. The failed-task enabled switch gates both
paths. Reliability callbacks are delivered immediately, outside failed-task batching.
The optional token is not returned by settings, exports or diagnostics.

Export one goal's snippets. The export contains a `prometheus.groups` document,
an `alertmanager_route` fragment and a `receiver` fragment. JSON is valid YAML.
Merge the group into Prometheus rule files, put the route under your existing root
route, add the receiver once, replace `https://YOUR-STUDIO` with the reachable HTTPS
origin, and mount the webhook token in the receiver's `credentials_file`. Configure
Alertmanager `max_alerts: 0` (the default) to avoid truncation. Keep grouped deliveries
at or below 100 alerts and 256 KiB by retaining the exported goal/alertname grouping.
Never put the secret in a browser, URL, annotation, or checked-in configuration.

The callback is `POST /studio/ingest/reliability-alerts` with
`Authorization: Bearer <dedicated token>` and the standard Alertmanager webhook
body. Its own credential is required in both Entra and shared-operator deployments;
browser sessions and CSRF tokens do not authorize it. Unknown goals, mismatched
revision/owner/service/environment labels, disabled services, environment drift,
truncated/oversized bodies and invalid timestamps are rejected. Deploy rules and
routing consistently with the saved goal. A deleted goal rejects future callbacks.

## Supported evidence and honest limitations

| Kind | Measurement | Requirements and limits |
| --- | --- | --- |
| Backlog age | Oldest ready work age, seconds | External queue-age aggregate plus actual observation timestamp. Queue depth is not age. |
| Retry activity | Sum of SDK retry counter increases over the window | `relayna_tasks_retried_total`; observed aggregate activity, not retries per unique task. Counter extrapolation and missing samples can under/overestimate activity; an observed value below threshold does not prove full window coverage. |
| Stale leases | Count of currently expired active leases | External fresh aggregate. Persistent SDK pressure gauges lack observation freshness and are not substituted. |
| Missing worker | `time() - max(relayna_worker_heartbeat_timestamp)` | Detects loss of all service heartbeats, not a missing member of a pool. An absent series is missing evidence, not zero workers. |
| Completion success | Successful-by-deadline eligible work / all eligible accepted work | External durable mature-cohort aggregates. No task-attempt denominator. |
| End-to-end latency | Successful work finished within deadline / all eligible accepted work | External durable mature-cohort aggregates. No handler-duration substitution. |

Preview reads the registered Prometheus instant API at one evaluation timestamp.
`observed` means a fresh value below the signal threshold or meeting the objective
at that instant; it is not a continuously evaluated healthy status. `breaching`
means that value crosses the goal. `no_data`, `stale`, `insufficient`, `unsupported`
and `unavailable` preserve absent/nonfinite values, old/future observations, invalid
cohorts, unconfigured scope/providers and failed/ambiguous upstream data. No eligible
work yields no ratio. Preview rejects partial-response warnings and duplicate series.
Exports guard freshness, finite values, aggregate cardinality and cohort boundaries,
and include a separate `RelaynaReliabilityEvidenceMissing` rule. A failing Prometheus
rule query is an evaluator error, not an empty result: monitor evaluator failures
through your existing Prometheus/Grafana health alerts.

## External aggregate contract

Studio does not produce these metrics. A business or infrastructure exporter must
maintain them from authoritative durable data, with exactly one series for each
metric and selector. Do not add task, run, worker, message or request IDs as labels.
Every custom aggregate has these labels:

    service="orders-api", environment="production", objective="end_to_end_latency",
    window_seconds="86400", deadline_seconds="120"

`service` uses the registered `runtime_service_label_value`, the metrics selector's
`service` value, or the Studio service ID, in that order. `objective` is the saved
kind; windows and deadline are exact integer seconds from that goal.

For backlog age, expose `relayna_reliability_backlog_oldest_age_seconds`; for stale
leases, expose `relayna_reliability_stale_leases`. Both require
`relayna_reliability_evidence_timestamp_seconds`: Unix seconds of the underlying
observation, **not** the scrape time. Zero is valid only after observing the full
scope with no backlog/expired leases. The aggregate must cover all expected queues
or workers in this service/environment; partial discovery is missing evidence.

Business exporters expose five gauges with the same selector:

- `relayna_reliability_good_work`: nonnegative count meeting the objective.
- `relayna_reliability_eligible_work`: all eligible accepted business identities,
  including unfinished, failed, and late work. Count each identity once.
- `relayna_reliability_window_start_timestamp_seconds`: cohort start, inclusive.
- `relayna_reliability_window_end_timestamp_seconds`: cohort end, exclusive.
- `relayna_reliability_evidence_timestamp_seconds`: actual source watermark.

At evaluation time `T`, deadline `D`, and window `W`, the acceptance cohort is
`[T - D - W, T - D)`. Each item has had its full deadline to finish. Completion
success counts items successfully terminal by `accepted_at + D`; latency counts
items successfully finished with `finished_at - accepted_at <= D`. These initial
objectives therefore share the same mature bounded completion criterion, with
different product intent. Future unbounded eventual-success objectives need their
own eligibility policy. Unfinished mature work remains in the denominator and
outside good work. Work accepted in the newest `D` seconds is not eligible yet.
Retries, child tasks and fan-out do not add business identities. Deduplicate by your
business identity in the authoritative exporter, never by Prometheus labels.

The end must align to `T-D` within 30 seconds and the cohort duration to `W` within
one second; exporter refresh and scrape/evaluation cadence must satisfy that bound.
The watermark must be neither future-dated nor older than the configured freshness.
`0 <= good <= eligible`; zero eligible is missing evidence. Export only complete
cohorts. With incomplete retention, missing source partitions or uncertain identity,
omit the aggregates or stop advancing the watermark. Do not report zero failures.

Studio events and task search default to 24-hour retention and are observational
projections, not an accepted-business-work ledger. A 30-day goal needs an exporter
with at least its full cohort plus deadline retained, or durable exact aggregates
with equivalent evidence. Configuring a 30-day window never establishes that
retention. The illustrative “99% of accepted orders finish within two minutes” is
a target an operator may configure, not a measured baseline of Studio or Relayna.

## Delivery diagnostics and investigations

The callback resolves recipients from the saved goal, ignoring arbitrary external
annotations and receiver addresses. It sends firing and resolved messages and stores
received/sent/duplicate counts, last callback time, state and a scoped Studio path.
Repeated callbacks for the same goal/alertname/start/status are suppressed; resolved
receipts prevent a delayed firing callback for that same episode from sending.
The last 100 episodes are retained per goal, so very old replayed episodes may be
sent again after eviction. Alertmanager remains the durable alert history. The bridge
suppresses Alertmanager repeat reminders for receipted episodes; use native receivers
for reminder/escalation policies. Store and lock scope is per goal across replicas.

A failed or disabled email transport records a diagnostic and returns non-2xx so
Alertmanager retries. A successful send followed by a crash before receipt commit
can send twice: delivery is at least once, not exactly once. Batch callbacks may
partly succeed; retrying deduplicates successful items. Callback/email error messages
are bounded generic diagnostics, without upstream secrets. The goal settings and
receipts persist until deletion; audit history uses existing retention conventions.

Investigation links carry service, environment and the original alert/preview time
window. An alert link can outlive Studio's event retention; missing retained evidence
must not be interpreted as resolution. The UI reports observed callbacks and duplicate
suppression, but does not claim early-detection lead time, incident reduction, or time
to first investigation: these require outcome and click instrumentation not yet
available. Future Cockpit wiring can replace the investigation destination for
queue/worker kinds without changing the goal, metric or callback identity.

API entry points: `GET/POST /studio/reliability/goals`,
`DELETE /studio/reliability/goals/{id}`, and
`GET /studio/reliability/goals/{id}/preview` or `/export`. Listing accepts
`service_id` and `environment`. These additive Studio surfaces are an explicitly
authorized production-freeze perimeter change; SDK and old responses are preserved.

Reference architecture: [Grafana notifications](https://grafana.com/docs/grafana/latest/alerting/configure-notifications/),
[Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/), and
[Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/).
