import { useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { requestJson } from "../api";
import type { LoadRun } from "../load-testing";
import { MonitorWorkspace } from "../monitor-workspace";
import type { ServiceRecord, StudioTaskSearchQuery, StudioTaskSearchResponse } from "../types";
import { NoticeBanner } from "../ui";
import type { Document } from "./common";

export function RunMonitor({ base, service, run, active }: { base: string; service: ServiceRecord; run: LoadRun; active: boolean }) {
  const [params, setParams] = useSearchParams();
  const ownedEnd = useRef("");
  useEffect(() => {
    if (params.get("monitor_run") === run.id) return;
    ownedEnd.current = run.finished_at || new Date().toISOString();
    setParams((old) => { const next = new URLSearchParams(old); next.set("monitor_run", run.id); next.set("monitor_window", "custom"); next.set("monitor_from", run.started_at || run.created_at); next.set("monitor_to", ownedEnd.current); next.delete("monitor_task"); next.set("monitor_scope", "service"); return next; }, { replace: true });
  }, [run.id, run.started_at, run.created_at, run.finished_at, params, setParams]);
  useEffect(() => {
    const advance = () => {
      if (params.get("monitor_run") !== run.id || params.get("monitor_window") !== "custom" || params.get("monitor_to") !== ownedEnd.current) return;
      if (run.finished_at === ownedEnd.current) return;
      setParams((old) => {
      if (old.get("monitor_run") !== run.id || old.get("monitor_window") !== "custom" || old.get("monitor_to") !== ownedEnd.current) return old;
      const end = run.finished_at || new Date().toISOString();
      if (end === ownedEnd.current) return old;
      ownedEnd.current = end;
      const next = new URLSearchParams(old); next.set("monitor_to", end); return next;
    }, { replace: true });
    };
    if (run.finished_at) advance();
    else if (active) { const timer = setInterval(advance, 30000); return () => clearInterval(timer); }
  }, [run.id, run.finished_at, active, params, setParams]);
  const search = useCallback(async (query: StudioTaskSearchQuery): Promise<StudioTaskSearchResponse> => {
    const runId = run.chamber?.run_id;
    const result: Document = runId ? await requestJson(`${base}/chamber/runs/${encodeURIComponent(runId)}/tasks?${new URLSearchParams({ reference: run.id, page: query.cursor || "1", page_size: "50", search: query.task_id || "", status: query.status || "", failed_first: "true" })}`) : { items: run.tasks || [], pagination: { page: 1, total_pages: 1 } };
    return { count: (result.items || []).length, items: (result.items || []).map((task: Document) => ({ service_id: service.service_id, service_name: service.name, environment: run.environment, task_id: task.task_id, correlation_id: task.correlation_id || null, status: task.terminal_status || "pending", stage: "Run task", first_seen_at: task.started_at || run.started_at || run.created_at, last_seen_at: task.completed_at || run.finished_at || "", detail_path: `/tasks/${encodeURIComponent(service.service_id)}/${encodeURIComponent(task.task_id)}` })), next_cursor: result.pagination?.page < result.pagination?.total_pages ? String(result.pagination.page + 1) : null };
  }, [base, run.id, run.chamber?.run_id, run.tasks, run.environment, run.started_at, run.created_at, run.finished_at, service.service_id, service.name]);
  return <><NoticeBanner tone="info">The task explorer lists exact tasks from this run. Service-scope logs and pod metrics may include other traffic; selecting a run task narrows its logs and events.</NoticeBanner><MonitorWorkspace key={run.id} service={service} active={active} taskSearch={search} taskSource="Run tasks" taskSourceKey={run.id} /></>;
}
