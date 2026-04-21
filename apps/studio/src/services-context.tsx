import type { ReactNode } from "react";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

import {
  createService,
  deleteService,
  listServices,
  refreshService,
  runHealthCheck,
  serviceToDraft,
  updateService,
  updateServiceStatus,
} from "./api";
import type { ServiceDraft, ServiceRecord, ServiceStatus } from "./types";

type ServicesContextValue = {
  services: ServiceRecord[];
  servicesById: Map<string, ServiceRecord>;
  loading: boolean;
  error: string | null;
  notice: string | null;
  emptyDraft: ServiceDraft;
  serviceToDraft: typeof serviceToDraft;
  reload: (preferredServiceId?: string | null) => Promise<void>;
  create: (draft: ServiceDraft) => Promise<ServiceRecord>;
  update: (serviceId: string, draft: ServiceDraft) => Promise<ServiceRecord>;
  refresh: (serviceId: string) => Promise<ServiceRecord>;
  runHealthCheck: (serviceId: string) => Promise<ServiceRecord>;
  updateStatus: (serviceId: string, status: ServiceStatus) => Promise<ServiceRecord>;
  remove: (serviceId: string) => Promise<void>;
  clearMessages: () => void;
};

const emptyDraft: ServiceDraft = {
  service_id: "",
  name: "",
  base_url: "https://service.example.test",
  environment: "dev",
  tags: "",
  auth_mode: "internal_network",
  log_provider: "",
  log_base_url: "",
  log_tenant_id: "",
  log_service_selector_labels: "",
  log_source_label: "",
  log_task_id_label: "",
  log_correlation_id_label: "",
  log_level_label: "",
};

const ServicesContext = createContext<ServicesContextValue | null>(null);

export function StudioServicesProvider({ children }: { children: ReactNode }) {
  const [services, setServices] = useState<ServiceRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function setMutationError(fetchError: unknown, fallback: string) {
    setNotice(null);
    setError(fetchError instanceof Error ? fetchError.message : fallback);
  }

  async function reload() {
    setLoading(true);
    try {
      const payload = await listServices();
      setServices(payload.services || []);
      setError(null);
    } catch (fetchError) {
      setError(fetchError instanceof Error ? fetchError.message : "Unable to load services.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  const value = useMemo<ServicesContextValue>(
    () => ({
      services,
      servicesById: new Map(services.map((service) => [service.service_id, service])),
      loading,
      error,
      notice,
      emptyDraft,
      serviceToDraft,
      reload,
      async create(draft) {
        try {
          const saved = await createService(draft);
          setNotice(`Registered service '${saved.service_id}'.`);
          setError(null);
          await reload();
          return saved;
        } catch (fetchError) {
          setMutationError(fetchError, "Unable to register service.");
          throw fetchError;
        }
      },
      async update(serviceId, draft) {
        try {
          const saved = await updateService(serviceId, draft);
          setNotice(`Updated service '${saved.service_id}'.`);
          setError(null);
          await reload();
          return saved;
        } catch (fetchError) {
          setMutationError(fetchError, `Unable to update service '${serviceId}'.`);
          throw fetchError;
        }
      },
      async refresh(serviceId) {
        try {
          const saved = await refreshService(serviceId);
          setNotice(`Refreshed '${saved.service_id}'.`);
          setError(null);
          await reload();
          return saved;
        } catch (fetchError) {
          setMutationError(fetchError, `Unable to refresh service '${serviceId}'.`);
          throw fetchError;
        }
      },
      async runHealthCheck(serviceId) {
        try {
          await runHealthCheck(serviceId);
          setNotice(`Ran health check for '${serviceId}'.`);
          setError(null);
          await reload();
          const updated = (await listServices()).services.find((service) => service.service_id === serviceId);
          if (!updated) {
            throw new Error(`Service '${serviceId}' was not found after health refresh.`);
          }
          setServices((current) =>
            current.map((service) => (service.service_id === serviceId ? updated : service)),
          );
          return updated;
        } catch (fetchError) {
          setMutationError(fetchError, `Unable to run health check for '${serviceId}'.`);
          throw fetchError;
        }
      },
      async updateStatus(serviceId, status) {
        try {
          const saved = await updateServiceStatus(serviceId, status);
          setNotice(`Marked '${serviceId}' as ${status}.`);
          setError(null);
          await reload();
          return saved;
        } catch (fetchError) {
          setMutationError(fetchError, `Unable to update service '${serviceId}'.`);
          throw fetchError;
        }
      },
      async remove(serviceId) {
        try {
          await deleteService(serviceId);
          setNotice(`Deleted service '${serviceId}'.`);
          setError(null);
          await reload();
        } catch (fetchError) {
          setMutationError(fetchError, `Unable to delete service '${serviceId}'.`);
          throw fetchError;
        }
      },
      clearMessages() {
        setError(null);
        setNotice(null);
      },
    }),
    [services, loading, error, notice],
  );

  return <ServicesContext.Provider value={value}>{children}</ServicesContext.Provider>;
}

export function useStudioServices() {
  const context = useContext(ServicesContext);
  if (!context) {
    throw new Error("useStudioServices must be used within StudioServicesProvider.");
  }
  return context;
}
