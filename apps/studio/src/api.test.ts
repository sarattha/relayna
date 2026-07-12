import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildServicePayload,
  createService,
  deleteFailedTask,
  deleteService,
  fetchBrokerDlq,
  fetchDlq,
  fetchFailedTaskDetail,
  fetchFailedTaskEmailSettings,
  fetchFailedTasks,
  fetchServiceEvents,
  fetchServiceLogs,
  fetchServiceMetrics,
  fetchTaskDetail,
  fetchTaskEvents,
  fetchTaskLogs,
  fetchTaskMetrics,
  fetchTaskTracePath,
  fetchTaskTraces,
  fetchTopology,
  formatLabelPairs,
  listGatewayServiceExports,
  listServices,
  markFailedTaskInvestigated,
  markFailedTaskUninvestigated,
  parseLabelPairs,
  refreshService,
  requestJson,
  retryFailedTask,
  runHealthCheck,
  searchServices,
  searchTasks,
  serviceToDraft,
  updateFailedTaskEmailSettings,
  updateService,
  updateServiceStatus,
} from "./api";
import type { ServiceDraft, ServiceRecord } from "./types";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyDraft(): ServiceDraft {
  return {
    service_id: " service/id ",
    name: " Service Name ",
    base_url: " https://service.example.test/ ",
    environment: " prod ",
    tags: " core, , money ",
    auth_mode: " internal_network ",
    log_provider: "",
    log_base_url: "",
    log_tenant_id: "",
    log_service_label_key: "",
    log_service_label_value: "",
    log_app_label_key: "",
    log_service_selector_labels: "",
    log_source_label: "",
    log_pod_label: "pod",
    log_pod_match_mode: "exact",
    log_pod_value_template: "{pod}",
    log_task_id_label: "",
    log_correlation_id_label: "",
    log_level_label: "",
    log_task_match_mode: "label",
    log_task_match_template: "",
    metrics_provider: "",
    metrics_base_url: "",
    metrics_namespace: "",
    metrics_service_label_key: "",
    metrics_service_label_value: "",
    metrics_service_selector_labels: "",
    metrics_runtime_service_label_value: "",
    metrics_namespace_label: "namespace",
    metrics_pod_label: "pod",
    metrics_container_label: "container",
    metrics_step_seconds: "30",
    metrics_task_window_padding_seconds: "120",
    trace_provider: "",
    trace_base_url: "",
    trace_public_base_url: "",
    trace_tenant_id: "",
    trace_query_path: "/api/traces/{trace_id}",
  };
}

describe("Studio API helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns JSON and reports structured and unstructured request failures", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ value: 3 }))
      .mockResolvedValueOnce(jsonResponse({ detail: "service unavailable" }, 503))
      .mockResolvedValueOnce(new Response("not-json", { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestJson<{ value: number }>("/ok")).resolves.toEqual({ value: 3 });
    await expect(requestJson("/detail-error")).rejects.toThrow("service unavailable");
    await expect(requestJson("/generic-error")).rejects.toThrow("Request failed with status 502.");
  });

  it("parses, formats, and maps service configuration with configured and default values", () => {
    expect(parseLabelPairs(" app = payments, malformed, empty=, =value, zone = west ")).toEqual({
      app: "payments",
      zone: "west",
    });
    expect(formatLabelPairs({ zone: "west", app: "payments" })).toBe("app=payments, zone=west");

    const minimalService = {
      service_id: "service-1",
      name: "Service",
      base_url: "https://service.example.test",
      environment: "prod",
      tags: [],
      auth_mode: "internal_network",
      status: "registered",
      capabilities: null,
      last_seen_at: null,
      log_config: null,
      metrics_config: null,
      trace_config: null,
      health: null,
    } satisfies ServiceRecord;
    const minimalDraft = serviceToDraft(minimalService);
    expect(minimalDraft).toMatchObject({
      log_provider: "",
      log_pod_label: "pod",
      metrics_step_seconds: "30",
      trace_query_path: "/api/traces/{trace_id}",
    });

    const configuredDraft = serviceToDraft({
      ...minimalService,
      tags: ["core", "money"],
      log_config: {
        provider: "loki",
        base_url: "https://loki.example.test",
        tenant_id: "tenant-a",
        service_selector_labels: { zone: "west", service: "payments" },
        source_label: "app",
        pod_label: "pod_name",
        pod_match_mode: "regex",
        pod_value_template: "^{pod}$",
        task_id_label: "task",
        correlation_id_label: "correlation",
        level_label: "severity",
        task_match_mode: "regex",
        task_match_template: "task={task_id}",
      },
      metrics_config: {
        provider: "prometheus",
        base_url: "https://prom.example.test",
        namespace: "payments",
        service_selector_labels: { app: "payments", zone: "west" },
        runtime_service_label_value: "payments-runtime",
        namespace_label: "ns",
        pod_label: "pod_name",
        container_label: "container_name",
        step_seconds: 45,
        task_window_padding_seconds: 180,
      },
      trace_config: {
        provider: "tempo",
        base_url: "https://tempo.example.test",
        public_base_url: "https://traces.example.test",
        tenant_id: "tenant-a",
        query_path: "/trace/{trace_id}",
      },
    });
    expect(configuredDraft).toMatchObject({
      tags: "core, money",
      log_service_label_key: "service",
      log_service_label_value: "payments",
      log_service_selector_labels: "zone=west",
      metrics_service_label_key: "app",
      metrics_service_selector_labels: "zone=west",
      metrics_step_seconds: "45",
      trace_public_base_url: "https://traces.example.test",
    });

    const selectorWithoutPreferredKey = serviceToDraft({
      ...minimalService,
      log_config: { ...configuredDraftToLogConfig(configuredDraft), service_selector_labels: { app: "payments" } },
    });
    expect(selectorWithoutPreferredKey.log_service_label_key).toBe("app");
  });

  it("builds optional log, metrics, and trace payloads for every configuration trigger", () => {
    const base = emptyDraft();
    expect(buildServicePayload(base)).toMatchObject({
      service_id: "service/id",
      name: "Service Name",
      tags: ["core", "money"],
      log_config: null,
      metrics_config: null,
      trace_config: null,
    });

    const logTriggers: Array<Partial<ServiceDraft>> = [
      { log_provider: "loki" },
      { log_base_url: "https://loki.example.test" },
      { log_service_label_key: "service" },
      { log_service_label_value: "payments" },
      { log_app_label_key: "app" },
      { log_service_selector_labels: "zone=west" },
      { log_pod_label: "pod_name" },
      { log_pod_match_mode: "regex" },
      { log_pod_value_template: "^{pod}$" },
      { log_task_id_label: "task_id" },
      { log_correlation_id_label: "correlation_id" },
      { log_level_label: "severity" },
      { log_task_match_template: "task={task_id}" },
      { log_task_match_mode: "contains" },
      { log_tenant_id: "tenant-a" },
    ];
    for (const trigger of logTriggers) {
      expect(buildServicePayload({ ...base, ...trigger }).log_config).not.toBeNull();
    }
    const logPayload = buildServicePayload({
      ...base,
      log_service_label_key: " service ",
      log_service_label_value: " payments ",
      log_service_selector_labels: "zone=west",
      log_pod_label: " ",
      log_pod_match_mode: "regex",
      log_pod_value_template: " ",
      log_task_match_mode: "regex",
      log_tenant_id: " tenant-a ",
    }).log_config;
    expect(logPayload).toMatchObject({
      provider: "loki",
      service_selector_labels: { service: "payments", zone: "west" },
      pod_label: "pod",
      pod_value_template: "{pod}",
      tenant_id: "tenant-a",
    });

    const metricTriggers: Array<Partial<ServiceDraft>> = [
      { metrics_provider: "prometheus" },
      { metrics_base_url: "https://prom.example.test" },
      { metrics_namespace: "payments" },
      { metrics_service_label_key: "service" },
      { metrics_service_label_value: "payments" },
      { metrics_service_selector_labels: "zone=west" },
      { metrics_runtime_service_label_value: "payments-runtime" },
    ];
    for (const trigger of metricTriggers) {
      expect(buildServicePayload({ ...base, ...trigger }).metrics_config).not.toBeNull();
    }
    const metricsPayload = buildServicePayload({
      ...base,
      metrics_service_label_key: " service ",
      metrics_service_label_value: " payments ",
      metrics_service_selector_labels: "zone=west",
      metrics_namespace_label: " ",
      metrics_pod_label: " ",
      metrics_container_label: " ",
      metrics_step_seconds: "not-a-number",
      metrics_task_window_padding_seconds: "0",
    }).metrics_config;
    expect(metricsPayload).toMatchObject({
      provider: "prometheus",
      service_selector_labels: { service: "payments", zone: "west" },
      namespace_label: "namespace",
      pod_label: "pod",
      container_label: "container",
      step_seconds: 30,
      task_window_padding_seconds: 120,
    });

    expect(buildServicePayload({ ...base, trace_provider: "tempo" }).trace_config).toBeNull();
    const traceTriggers: Array<Partial<ServiceDraft>> = [
      { trace_base_url: "https://tempo.example.test" },
      { trace_public_base_url: "https://traces.example.test" },
      { trace_tenant_id: "tenant-a" },
      { trace_query_path: "/trace/{trace_id}" },
    ];
    for (const trigger of traceTriggers) {
      const trace = buildServicePayload({ ...base, trace_provider: "tempo", ...trigger }).trace_config;
      expect(trace).not.toBeNull();
    }
    expect(
      buildServicePayload({
        ...base,
        trace_provider: "tempo",
        trace_base_url: "https://tempo.example.test",
        trace_query_path: " ",
      }).trace_config,
    ).toMatchObject({ provider: "tempo", query_path: "/api/traces/{trace_id}" });
  });

  it("calls every mutation and fixed-path endpoint with encoded identifiers", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);
    const draft = emptyDraft();

    await listServices();
    await listGatewayServiceExports();
    await createService(draft);
    await updateService("service/id", draft);
    await updateServiceStatus("service/id", "disabled");
    await refreshService("service/id");
    await runHealthCheck("service/id");
    await deleteService("service/id");
    await fetchTaskEvents("service/id", "task/id", 7);
    await fetchTaskTraces("service/id", "task/id");
    await fetchTaskTracePath("service/id", "task/id");
    await fetchTaskDetail("service/id", "task/id", "none");
    await fetchTopology("service/id");
    await fetchFailedTaskEmailSettings();
    await updateFailedTaskEmailSettings({ enabled: true, batch_wait_seconds: 15 });
    await fetchFailedTaskDetail("service/id", "failure/id");
    await markFailedTaskInvestigated("service/id", "failure/id", { investigated_by: "ops", note: "checked" });
    await markFailedTaskUninvestigated("service/id", "failure/id");
    await retryFailedTask("service/id", "failure/id", { target_queue: "retry", override_payload: { safe: true } });
    await deleteFailedTask("service/id", "failure/id");

    const calls = fetchMock.mock.calls.map(([input, init]) => [String(input), init?.method || "GET"]);
    expect(calls).toContainEqual(["/studio/services/service%2Fid", "PATCH"]);
    expect(calls).toContainEqual(["/studio/tasks/service%2Fid/task%2Fid?join=none", "GET"]);
    expect(calls).toContainEqual(["/studio/failed-tasks/service%2Fid/failure%2Fid", "DELETE"]);
    expect(calls).toHaveLength(20);
  });

  it("builds full and minimal query strings for logs, metrics, search, and DLQ requests", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    await fetchServiceEvents("service/id");
    await fetchServiceEvents("service/id", { limit: 7, from: " 2026-01-01 ", to: " 2026-01-02 " });
    await fetchServiceLogs("service/id", {});
    await fetchServiceLogs("service/id", {
      query: " timeout ", level: " error ", source: " worker ", pod: " pod-1 ", limit: 7,
      from: " 2026-01-01 ", to: " 2026-01-02 ",
    });
    await fetchTaskLogs("service/id", "task/id", {});
    await fetchTaskLogs("service/id", "task/id", {
      query: " timeout ", level: " error ", source: " worker ", pod: " pod-1 ", limit: 7,
      correlation_id: " corr/id ", from: " 2026-01-01 ", to: " 2026-01-02 ",
    });
    await fetchServiceMetrics("service/id");
    await fetchServiceMetrics("service/id", {
      from: " 2026-01-01 ", to: " 2026-01-02 ", step: 15, groups: ["cpu_usage", "memory_usage"],
      pod: " pod-1 ", split_by_pod: true,
    });
    await fetchTaskMetrics("service/id", "task/id");
    await fetchTaskMetrics("service/id", "task/id", {
      from: " 2026-01-01 ", to: " 2026-01-02 ", step: 30, groups: ["task_duration_p95"], pod: " pod-1 ",
      split_by_pod: true,
    });
    await searchTasks({});
    await searchTasks({
      service_id: " service/id ", task_id: " task/id ", correlation_id: " corr/id ", status: " failed ",
      stage: " charge ", from: " 2026-01-01 ", to: " 2026-01-02 ", cursor: " next token ", limit: 7,
    });
    await searchServices({});
    await searchServices({
      query: " payment ", environment: " prod ", status: " healthy ", health: " stale ", tag: " core ",
      cursor: " next token ", limit: 7,
    });
    await fetchDlq("service/id", {
      queue_name: "", task_id: "", reason: "", source_queue_name: "", state: "", limit: "", cursor: null,
    });
    await fetchDlq("service/id", {
      queue_name: " dlq ", task_id: " task/id ", reason: " timeout ", source_queue_name: " source ",
      state: " dead_lettered ", limit: "7", cursor: " next token ",
    });
    await fetchBrokerDlq("service/id", { queue_name: "", task_id: "", limit: "" });
    await fetchBrokerDlq("service/id", { queue_name: " dlq ", task_id: " task/id ", limit: "7" });
    await fetchFailedTasks({
      service_id: "", queue_name: "", dlq_name: "", error_type: "", status: "", task_id: "", worker_id: "",
      investigation_status: "", failed_from: "", failed_to: "", limit: "", cursor: null,
    });
    await fetchFailedTasks({
      service_id: " service/id ", queue_name: " queue ", dlq_name: " dlq ", error_type: " timeout ",
      status: " failed ", task_id: " task/id ", worker_id: " worker-1 ", investigation_status: " unreviewed ",
      failed_from: " 2026-01-01 ", failed_to: " 2026-01-02 ", limit: "7", cursor: " next token ",
    });

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls).toContain("/studio/services/service%2Fid/events?limit=20");
    expect(urls).toContain(
      "/studio/services/service%2Fid/metrics?from=2026-01-01&to=2026-01-02&step=15&pod=pod-1&split_by_pod=true&group=cpu_usage&group=memory_usage",
    );
    expect(urls).toContain(
      "/studio/tasks/search?service_id=service%2Fid&task_id=task%2Fid&correlation_id=corr%2Fid&status=failed&stage=charge&from=2026-01-01&to=2026-01-02&cursor=next+token&limit=7",
    );
    expect(urls).toContain("/studio/services/service%2Fid/broker/dlq/messages?limit=50");
    expect(urls).toContain("/studio/failed-tasks?limit=50");
  });
});

function configuredDraftToLogConfig(draft: ServiceDraft) {
  return {
    provider: "loki" as const,
    base_url: draft.log_base_url,
    tenant_id: draft.log_tenant_id,
    service_selector_labels: { service: draft.log_service_label_value },
    source_label: draft.log_source_label,
    pod_label: draft.log_pod_label,
    pod_match_mode: draft.log_pod_match_mode,
    pod_value_template: draft.log_pod_value_template,
    task_id_label: draft.log_task_id_label,
    correlation_id_label: draft.log_correlation_id_label,
    level_label: draft.log_level_label,
    task_match_mode: draft.log_task_match_mode,
    task_match_template: draft.log_task_match_template,
  };
}
