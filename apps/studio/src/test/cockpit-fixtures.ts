import type { CockpitResponse, ServiceRecord } from "../types";

export const cockpitService: ServiceRecord = {
  service_id: "orders",
  name: "Orders",
  environment: "prod",
  base_url: "https://orders.example.test",
  tags: [],
  auth_mode: "internal_network",
  status: "healthy",
};

export function cockpitFixture(now = Date.now()): CockpitResponse {
  const timestamp = new Date(now).toISOString();
  const from = new Date(now - 900_000).toISOString();
  const series = (
    metric:
      | "tasks_retried_rate"
      | "tasks_dlq_rate"
      | "active_tasks"
      | "task_duration_p95",
  ) => ({
    metric,
    unit: metric.includes("rate") ? "per_second" : "count",
    labels: {},
    points: [
      { timestamp: from, value: 1 },
      { timestamp: new Date(now - 450_000).toISOString(), value: null },
      { timestamp, value: metric === "active_tasks" ? 2 : 0 },
    ],
  });
  return {
    service_id: "orders",
    reported_at: timestamp,
    stale_after_seconds: 90,
    worker_stale_after_seconds: 90,
    sources: {
      pressure: {
        state: "fresh",
        detail: "Configured queue observations.",
        reported_at: timestamp,
      },
      workers: {
        state: "fresh",
        detail: "Worker health provider.",
        reported_at: timestamp,
      },
      metrics: { state: "fresh", detail: "Prometheus service scope." },
    },
    queues: [
      {
        queue_name: "orders.tasks",
        source: "configured_broker",
        observed_at: timestamp,
        freshness: "fresh",
        ready: 120,
        unacknowledged: 2,
        consumers: 1,
        oldest_message_age_seconds: 600,
        arrival_rate: 12,
        completion_rate: 8,
        rate_window_seconds: 300,
        diagnosis:
          "Arrival rate meets or exceeds completion rate; this queue is not draining.",
        drain: {
          state: "not_draining",
          seconds: null,
          reason: "Arrival rate meets or exceeds completion rate.",
        },
      },
    ],
    workers: [
      {
        worker_name: "orders-worker-1",
        liveness: "unknown",
        running: true,
        last_heartbeat_at: timestamp,
        freshness: "fresh",
        state: "fresh",
        active_leases: [
          {
            lease_id: "lease-123",
            task_id: "task/123",
            owner_id: "pod-orders-1",
            consumer_name: "orders-worker-1",
            heartbeat_at: new Date(now - 180_000).toISOString(),
            acquired_at: new Date(now - 600_000).toISOString(),
            expires_at: new Date(now - 60_000).toISOString(),
            expired: false,
            attempt: 2,
            stage: "capture",
            freshness: "stale",
            age_seconds: 600,
          },
        ],
      },
    ],
    signals: [
      {
        scope: "queue",
        scope_id: "orders.tasks",
        kind: "queue_depth_high",
        severity: "warning",
        value: 120,
        reason: "Ready work is waiting.",
        recommended_action: "Inspect consumer coverage and handler latency.",
        observed_at: timestamp,
        freshness: "fresh",
      },
    ],
    metrics: {
      from,
      to: timestamp,
      scope: "service",
      rate_window_seconds: 300,
      warnings: [],
      series: [
        series("tasks_retried_rate"),
        series("tasks_dlq_rate"),
        series("active_tasks"),
        series("task_duration_p95"),
      ],
    },
  };
}
