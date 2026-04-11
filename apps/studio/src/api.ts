import type {
  DlqMessageListResponse,
  DlqQueryState,
  ServiceDraft,
  ServiceListResponse,
  ServiceRecord,
  ServiceStatus,
  StudioEventListResponse,
  StudioLogListResponse,
  StudioTaskDetail,
  StudioTaskSearchResponse,
  WorkflowTopologyResponse,
} from "./types";

export async function requestJson<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
  if (!response.ok) {
    throw new Error(payload?.detail || `Request failed with status ${response.status}.`);
  }
  return payload as T;
}

export function parseLabelPairs(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((accumulator, item) => {
      const separatorIndex = item.indexOf("=");
      if (separatorIndex <= 0) {
        return accumulator;
      }
      const key = item.slice(0, separatorIndex).trim();
      const parsedValue = item.slice(separatorIndex + 1).trim();
      if (!key || !parsedValue) {
        return accumulator;
      }
      accumulator[key] = parsedValue;
      return accumulator;
    }, {});
}

export function formatLabelPairs(value: Record<string, string>) {
  return Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, itemValue]) => `${key}=${itemValue}`)
    .join(", ");
}

export function serviceToDraft(service: ServiceRecord): ServiceDraft {
  return {
    service_id: service.service_id,
    name: service.name,
    base_url: service.base_url,
    environment: service.environment,
    tags: service.tags.join(", "),
    auth_mode: service.auth_mode,
    log_provider: service.log_config?.provider || "",
    log_base_url: service.log_config?.base_url || "",
    log_tenant_id: service.log_config?.tenant_id || "",
    log_service_selector_labels: formatLabelPairs(service.log_config?.service_selector_labels || {}),
    log_task_id_label: service.log_config?.task_id_label || "",
    log_correlation_id_label: service.log_config?.correlation_id_label || "",
    log_level_label: service.log_config?.level_label || "",
  };
}

export function buildServicePayload(draft: ServiceDraft) {
  const hasLogConfig = Boolean(
    draft.log_provider ||
      draft.log_base_url.trim() ||
      draft.log_service_selector_labels.trim() ||
      draft.log_task_id_label.trim() ||
      draft.log_correlation_id_label.trim() ||
      draft.log_level_label.trim() ||
      draft.log_tenant_id.trim(),
  );

  return {
    service_id: draft.service_id.trim(),
    name: draft.name.trim(),
    base_url: draft.base_url.trim(),
    environment: draft.environment.trim(),
    tags: draft.tags
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    auth_mode: draft.auth_mode.trim(),
    log_config: hasLogConfig
      ? {
          provider: (draft.log_provider || "loki") as "loki",
          base_url: draft.log_base_url.trim(),
          tenant_id: draft.log_tenant_id.trim() || null,
          service_selector_labels: parseLabelPairs(draft.log_service_selector_labels),
          task_id_label: draft.log_task_id_label.trim() || null,
          correlation_id_label: draft.log_correlation_id_label.trim() || null,
          level_label: draft.log_level_label.trim() || null,
        }
      : null,
  };
}

export async function listServices() {
  return requestJson<ServiceListResponse>("/studio/services");
}

export async function createService(draft: ServiceDraft) {
  return requestJson<ServiceRecord>("/studio/services", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildServicePayload(draft)),
  });
}

export async function updateService(serviceId: string, draft: ServiceDraft) {
  const payload = buildServicePayload(draft);
  return requestJson<ServiceRecord>(`/studio/services/${encodeURIComponent(serviceId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: payload.name,
      base_url: payload.base_url,
      environment: payload.environment,
      tags: payload.tags,
      auth_mode: payload.auth_mode,
      log_config: payload.log_config,
    }),
  });
}

export async function updateServiceStatus(serviceId: string, status: ServiceStatus) {
  return requestJson<ServiceRecord>(`/studio/services/${encodeURIComponent(serviceId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}

export async function refreshService(serviceId: string) {
  return requestJson<ServiceRecord>(`/studio/services/${encodeURIComponent(serviceId)}/refresh`, {
    method: "POST",
  });
}

export async function deleteService(serviceId: string) {
  return requestJson<Record<string, unknown>>(`/studio/services/${encodeURIComponent(serviceId)}`, {
    method: "DELETE",
  });
}

export async function fetchServiceEvents(serviceId: string, limit = 20) {
  return requestJson<StudioEventListResponse>(
    `/studio/services/${encodeURIComponent(serviceId)}/events?${new URLSearchParams({ limit: String(limit) }).toString()}`,
  );
}

export async function fetchTaskEvents(serviceId: string, taskId: string, limit = 50) {
  return requestJson<StudioEventListResponse>(
    `/studio/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(taskId)}/events?${new URLSearchParams({
      limit: String(limit),
    }).toString()}`,
  );
}

export async function fetchServiceLogs(
  serviceId: string,
  query: { query?: string; level?: string; limit?: number },
) {
  const params = new URLSearchParams({ limit: String(query.limit || 20) });
  if (query.query?.trim()) {
    params.set("query", query.query.trim());
  }
  if (query.level?.trim()) {
    params.set("level", query.level.trim());
  }
  return requestJson<StudioLogListResponse>(
    `/studio/services/${encodeURIComponent(serviceId)}/logs?${params.toString()}`,
  );
}

export async function fetchTaskLogs(
  serviceId: string,
  taskId: string,
  query: { query?: string; level?: string; limit?: number; correlation_id?: string | null },
) {
  const params = new URLSearchParams({ limit: String(query.limit || 50) });
  if (query.query?.trim()) {
    params.set("query", query.query.trim());
  }
  if (query.level?.trim()) {
    params.set("level", query.level.trim());
  }
  if (query.correlation_id?.trim()) {
    params.set("correlation_id", query.correlation_id.trim());
  }
  return requestJson<StudioLogListResponse>(
    `/studio/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(taskId)}/logs?${params.toString()}`,
  );
}

export async function fetchTaskDetail(serviceId: string, taskId: string, join = "all") {
  const params = new URLSearchParams({ join });
  return requestJson<StudioTaskDetail>(
    `/studio/tasks/${encodeURIComponent(serviceId)}/${encodeURIComponent(taskId)}?${params.toString()}`,
  );
}

export async function searchTasks(taskId: string, join = "none") {
  const params = new URLSearchParams({ task_id: taskId.trim(), join });
  return requestJson<StudioTaskSearchResponse>(`/studio/tasks/search?${params.toString()}`);
}

export async function fetchTopology(serviceId: string) {
  return requestJson<WorkflowTopologyResponse>(`/studio/services/${encodeURIComponent(serviceId)}/workflow/topology`);
}

export async function fetchDlq(serviceId: string, state: DlqQueryState) {
  const params = new URLSearchParams({ limit: state.limit || "50" });
  if (state.queue_name.trim()) {
    params.set("queue_name", state.queue_name.trim());
  }
  if (state.task_id.trim()) {
    params.set("task_id", state.task_id.trim());
  }
  if (state.reason.trim()) {
    params.set("reason", state.reason.trim());
  }
  if (state.source_queue_name.trim()) {
    params.set("source_queue_name", state.source_queue_name.trim());
  }
  if (state.state.trim()) {
    params.set("state", state.state.trim());
  }
  if (state.cursor?.trim()) {
    params.set("cursor", state.cursor.trim());
  }
  return requestJson<DlqMessageListResponse>(
    `/studio/services/${encodeURIComponent(serviceId)}/dlq/messages?${params.toString()}`,
  );
}
