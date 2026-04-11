export type ServiceStatus = "registered" | "healthy" | "unavailable" | "disabled";

export type ServiceLogConfig = {
  provider: "loki";
  base_url: string;
  tenant_id?: string | null;
  service_selector_labels: Record<string, string>;
  task_id_label?: string | null;
  correlation_id_label?: string | null;
  level_label?: string | null;
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
  log_service_selector_labels: string;
  log_task_id_label: string;
  log_correlation_id_label: string;
  log_level_label: string;
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
  task_id: string;
  task_ref: StudioTaskRef;
  service_name: string;
  environment: string;
  latest_status: Record<string, unknown>;
  detail_path: string;
};

export type StudioJoinedTaskSearchItem = StudioTaskSearchItem & {
  join_kind: JoinKind;
  matched_value: string;
};

export type StudioTaskSearchResponse = {
  count: number;
  items: StudioTaskSearchItem[];
  joined_count: number;
  joined_items: StudioJoinedTaskSearchItem[];
  join_warnings: StudioJoinWarning[];
  errors: FederatedError[];
  scanned_services: string[];
};

export type StudioLogEntry = {
  service_id: string;
  task_id?: string | null;
  correlation_id?: string | null;
  timestamp: string;
  level?: string | null;
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

export type DlqQueryState = {
  queue_name: string;
  task_id: string;
  reason: string;
  source_queue_name: string;
  state: string;
  limit: string;
  cursor?: string | null;
};
