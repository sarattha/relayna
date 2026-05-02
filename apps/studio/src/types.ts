export type ServiceStatus = "registered" | "healthy" | "unavailable" | "disabled";
export type HealthStatus = "healthy" | "degraded" | "stale" | "unreachable" | "disabled" | "unknown";
export type HttpReachability = "reachable" | "unreachable" | "unknown";
export type CapabilityHealthState = "fresh" | "stale" | "missing" | "error";
export type ObservationFreshnessState = "fresh" | "stale" | "missing";
export type WorkerHealthState = "healthy" | "stale" | "unhealthy" | "unsupported" | "unknown";

export type HttpStatusSummary = {
  state: HttpReachability;
  checked_at?: string | null;
  error_detail?: string | null;
};

export type CapabilityHealth = {
  state: CapabilityHealthState;
  checked_at?: string | null;
  last_successful_at?: string | null;
  error_detail?: string | null;
};

export type ObservationFreshness = {
  state: ObservationFreshnessState;
  latest_status_event_at?: string | null;
  latest_observation_event_at?: string | null;
  latest_ingested_at?: string | null;
};

export type WorkerHeartbeatSummary = {
  worker_name: string;
  running: boolean;
  last_heartbeat_at?: string | null;
};

export type WorkerHealth = {
  state: WorkerHealthState;
  reported_at?: string | null;
  latest_heartbeat_at?: string | null;
  workers: WorkerHeartbeatSummary[];
  detail?: string | null;
};

export type ServiceHealthSummary = {
  service_id: string;
  registry_status: ServiceStatus;
  http_status: HttpStatusSummary;
  capability_status: CapabilityHealth;
  observation_freshness: ObservationFreshness;
  worker_health: WorkerHealth;
  last_checked_at?: string | null;
  overall_status: HealthStatus;
};

export type ServiceLogConfig = {
  provider: "loki";
  base_url: string;
  tenant_id?: string | null;
  service_selector_labels: Record<string, string>;
  source_label?: string | null;
  task_id_label?: string | null;
  correlation_id_label?: string | null;
  level_label?: string | null;
  task_match_mode?: "label" | "contains" | "regex";
  task_match_template?: string | null;
};

export type ServiceMetricsConfig = {
  provider: "prometheus";
  base_url: string;
  namespace: string;
  service_selector_labels: Record<string, string>;
  namespace_label: string;
  pod_label: string;
  container_label: string;
  step_seconds: number;
  task_window_padding_seconds: number;
};

export type ServiceRecord = {
  service_id: string;
  name: string;
  base_url: string;
  environment: string;
  tags: string[];
  auth_mode: string;
  status: ServiceStatus;
  capabilities?: Record<string, unknown> | null;
  last_seen_at?: string | null;
  log_config?: ServiceLogConfig | null;
  metrics_config?: ServiceMetricsConfig | null;
  health?: ServiceHealthSummary | null;
};

export type ServiceListResponse = {
  count: number;
  services: ServiceRecord[];
};

export type ServiceDraft = {
  service_id: string;
  name: string;
  base_url: string;
  environment: string;
  tags: string;
  auth_mode: string;
  log_provider: "" | "loki";
  log_base_url: string;
  log_tenant_id: string;
  log_service_label_key: string;
  log_service_label_value: string;
  log_app_label_key: string;
  log_service_selector_labels: string;
  log_source_label: string;
  log_task_id_label: string;
  log_correlation_id_label: string;
  log_level_label: string;
  log_task_match_mode: "label" | "contains" | "regex";
  log_task_match_template: string;
  metrics_provider: "" | "prometheus";
  metrics_base_url: string;
  metrics_namespace: string;
  metrics_service_label_key: string;
  metrics_service_label_value: string;
  metrics_service_selector_labels: string;
  metrics_namespace_label: string;
  metrics_pod_label: string;
  metrics_container_label: string;
  metrics_step_seconds: string;
  metrics_task_window_padding_seconds: string;
};

export type StudioTaskPointer = {
  service_id: string;
  task_id: string;
};

export type StudioTaskRef = {
  service_id: string;
  task_id: string;
  correlation_id?: string | null;
  parent_refs: StudioTaskPointer[];
  child_refs: StudioTaskPointer[];
};

export type JoinKind = "correlation_id" | "parent_task_id" | "workflow_lineage";

export type StudioTaskJoin = {
  task_ref: StudioTaskRef;
  join_kind: JoinKind;
  matched_value: string;
};

export type StudioJoinWarning = {
  code: string;
  detail: string;
  join_kind?: JoinKind | null;
  matched_value?: string | null;
};

export type ExecutionGraphNode = {
  id: string;
  kind: string;
  task_id?: string | null;
  service_id?: string | null;
  task_ref?: StudioTaskRef | null;
  label?: string | null;
  timestamp?: string | null;
  annotations?: Record<string, unknown>;
};

export type ExecutionGraphEdge = {
  source: string;
  target: string;
  kind: string;
  timestamp?: string | null;
  annotations?: Record<string, unknown>;
};

export type ExecutionGraphSummary = {
  status?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  graph_completeness: string;
};

export type ExecutionGraph = {
  service_id?: string;
  task_id: string;
  task_ref?: StudioTaskRef | null;
  topology_kind: string;
  summary: ExecutionGraphSummary;
  nodes: ExecutionGraphNode[];
  edges: ExecutionGraphEdge[];
  annotations: Record<string, unknown>;
  related_task_ids: string[];
};

export type FederatedError = {
  detail: string;
  code: string;
  service_id?: string | null;
  upstream_status?: number | null;
  retryable: boolean;
};

export type StatusPayload = {
  service_id: string;
  task_id: string;
  task_ref?: StudioTaskRef | null;
  event: Record<string, unknown>;
};

export type HistoryPayload = {
  service_id: string;
  task_id?: string | null;
  task_ref?: StudioTaskRef | null;
  count: number;
  events: Array<Record<string, unknown>>;
};

export type ServiceEventSourceKind = "status" | "observation";

export type StudioControlPlaneEvent = {
  service_id: string;
  ingest_method: "push" | "pull";
  ingested_at: string;
  dedupe_key: string;
  out_of_order: boolean;
  task_id: string;
  event_type: string;
  source_kind: ServiceEventSourceKind;
  component?: string | null;
  timestamp?: string | null;
  event_id?: string | null;
  correlation_id?: string | null;
  parent_task_id?: string | null;
  payload: Record<string, unknown>;
};

export type StudioEventListResponse = {
  count: number;
  items: StudioControlPlaneEvent[];
  next_cursor?: string | null;
};

export type DlqMessageSummary = {
  service_id: string;
  dlq_id: string;
  queue_name: string;
  source_queue_name: string;
  retry_queue_name: string;
  task_id?: string | null;
  correlation_id?: string | null;
  reason: string;
  exception_type?: string | null;
  retry_attempt: number;
  max_retries: number;
  content_type?: string | null;
  body_encoding: string;
  dead_lettered_at: string;
  state: string;
  replay_count: number;
  replayed_at?: string | null;
  replay_target_queue_name?: string | null;
  task_ref?: StudioTaskRef | null;
};

export type DlqMessageListResponse = {
  service_id: string;
  items: DlqMessageSummary[];
  next_cursor?: string | null;
};

export type BrokerDlqMessage = {
  service_id: string;
  queue_name: string;
  message_key: string;
  task_id?: string | null;
  correlation_id?: string | null;
  reason?: string | null;
  source_queue_name?: string | null;
  content_type?: string | null;
  body_encoding: string;
  dead_lettered_at?: string | null;
  headers: Record<string, unknown>;
  body: unknown;
  raw_body_b64: string;
  redelivered?: boolean | null;
  task_ref?: StudioTaskRef | null;
};

export type BrokerDlqMessageListResponse = {
  service_id: string;
  items: BrokerDlqMessage[];
};

export type TaskDlqMessagesPayload = {
  service_id: string;
  items: DlqMessageSummary[];
  next_cursor?: string | null;
};

export type StudioTaskDetail = {
  service: ServiceRecord;
  service_id: string;
  task_id: string;
  task_ref: StudioTaskRef;
  latest_status?: StatusPayload | null;
  history?: HistoryPayload | null;
  dlq_messages?: TaskDlqMessagesPayload | null;
  execution_graph?: ExecutionGraph | null;
  joined_refs: StudioTaskJoin[];
  join_warnings: StudioJoinWarning[];
  errors: FederatedError[];
};

export type StudioTaskSearchItem = {
  service_id: string;
  service_name: string;
  environment: string;
  task_id: string;
  correlation_id?: string | null;
  status?: string | null;
  stage?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  latest_event_type?: string | null;
  latest_event_at?: string | null;
  latest_ingested_at?: string | null;
  detail_path: string;
};

export type StudioTaskSearchResponse = {
  count: number;
  items: StudioTaskSearchItem[];
  next_cursor?: string | null;
};

export type StudioTaskSearchQuery = {
  service_id?: string;
  task_id?: string;
  correlation_id?: string;
  status?: string;
  stage?: string;
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string | null;
};

export type StudioServiceSearchItem = {
  service_id: string;
  name: string;
  environment: string;
  tags: string[];
  status: ServiceStatus;
  health_status?: HealthStatus | null;
  base_url: string;
  auth_mode: string;
  last_seen_at?: string | null;
  matched_fields: string[];
};

export type StudioServiceSearchResponse = {
  count: number;
  items: StudioServiceSearchItem[];
  next_cursor?: string | null;
};

export type StudioServiceSearchQuery = {
  query?: string;
  environment?: string;
  status?: string;
  health?: string;
  tag?: string;
  limit?: number;
  cursor?: string | null;
};

export type StudioLogEntry = {
  service_id: string;
  task_id?: string | null;
  correlation_id?: string | null;
  timestamp: string;
  level?: string | null;
  source: string;
  message: string;
  fields: Record<string, unknown>;
};

export type StudioLogListResponse = {
  count: number;
  items: StudioLogEntry[];
  next_cursor?: string | null;
};

export type WorkflowStage = {
  id?: string | null;
  name: string;
  queue: string;
  binding_keys: string[];
  publish_routing_key: string;
  queue_arguments: Record<string, unknown>;
  description?: string | null;
  role?: string | null;
  owner?: string | null;
  tags: string[];
  sla_ms?: number | null;
  accepted_actions: Array<Record<string, unknown>>;
  produced_actions: Array<Record<string, unknown>>;
  allowed_next_stages: string[];
  terminal: boolean;
  timeout_seconds?: number | null;
  max_retries?: number | null;
  retry_delay_ms?: number | null;
  max_inflight?: number | null;
  dedup_key_fields: string[];
};

export type WorkflowEntryRoute = {
  name: string;
  routing_key: string;
  target_stage: string;
};

export type WorkflowTopologyGraph = {
  workflow_exchange?: string | null;
  status_queue: string;
  stages: WorkflowStage[];
  entry_routes: WorkflowEntryRoute[];
  edges: Array<Record<string, unknown>>;
};

export type WorkflowTopologyResponse = WorkflowTopologyGraph & {
  service_id?: string;
};

export type StudioLogQuery = {
  query?: string;
  level?: string;
  limit?: number;
  correlation_id?: string | null;
};

export type StudioMetricGroup =
  | "cpu_usage"
  | "memory_usage"
  | "cpu_requests"
  | "cpu_limits"
  | "memory_requests"
  | "memory_limits"
  | "restarts"
  | "oom_killed"
  | "pod_phase"
  | "readiness"
  | "network_receive"
  | "network_transmit"
  | "tasks_started_rate"
  | "tasks_failed_rate"
  | "tasks_retried_rate"
  | "tasks_dlq_rate"
  | "task_duration_p95"
  | "active_tasks"
  | "worker_heartbeat"
  | "queue_publish_rate"
  | "status_events_rate"
  | "observation_events_rate";

export type StudioMetricPoint = {
  timestamp: string;
  value: number | null;
};

export type StudioMetricSeries = {
  metric: StudioMetricGroup;
  unit: string;
  labels: Record<string, string>;
  points: StudioMetricPoint[];
};

export type StudioMetricsResponse = {
  service_id: string;
  task_id?: string | null;
  from: string;
  to: string;
  step_seconds: number;
  approximate: boolean;
  warnings: string[];
  series: StudioMetricSeries[];
};

export type DlqMode = "indexed" | "broker";

export type DlqQueryState = {
  queue_name: string;
  task_id: string;
  reason: string;
  source_queue_name: string;
  state: string;
  limit: string;
  cursor?: string | null;
};
