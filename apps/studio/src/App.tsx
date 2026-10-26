import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { StudioServicesProvider } from "./services-context";
import { StudioAuthProvider } from "./auth-context";
import { AppChrome, AppHeader } from "./ui";

const QueueWorkerPage = lazy(() => import("./pages/QueueWorkerPage").then((module) => ({ default: module.QueueWorkerPage })));
const DlqPage = lazy(() => import("./pages/DlqPage").then((module) => ({ default: module.DlqPage })));
const FailedTasksPage = lazy(() =>
  import("./pages/FailedTasksPage").then((module) => ({ default: module.FailedTasksPage })),
);
const IncidentInboxPage = lazy(() => import("./pages/IncidentInboxPage").then((module) => ({ default: module.IncidentInboxPage })));
const OverviewPage = lazy(() =>
  import("./pages/OverviewPage").then((module) => ({ default: module.OverviewPage })),
);
const ServiceDetailPage = lazy(() =>
  import("./pages/ServiceDetailPage").then((module) => ({ default: module.ServiceDetailPage })),
);
const ServicesPage = lazy(() =>
  import("./pages/ServicesPage").then((module) => ({ default: module.ServicesPage })),
);
const TaskDetailPage = lazy(() =>
  import("./pages/TaskDetailPage").then((module) => ({ default: module.TaskDetailPage })),
);
const TaskSearchPage = lazy(() =>
  import("./pages/TaskSearchPage").then((module) => ({ default: module.TaskSearchPage })),
);
const TopologyPage = lazy(() =>
  import("./pages/TopologyPage").then((module) => ({ default: module.TopologyPage })),
);
const LoadTestingPage = lazy(() => import("./pages/LoadTestingPage").then((module) => ({ default: module.LoadTestingPage })));
const ReliabilityPage = lazy(() => import("./pages/ReliabilityPage").then((module) => ({ default: module.ReliabilityPage })));
const AccessPage = lazy(() => import("./pages/AccessPage").then((module) => ({ default: module.AccessPage })));
const RecoveryCenterPage = lazy(() => import("./pages/RecoveryCenterPage").then((module) => ({ default: module.RecoveryCenterPage })));

const HealthPage = lazy(() => import("./pages/HealthPage").then((module) => ({ default: module.HealthPage })));

export function App() {
  return (
    <StudioAuthProvider>
      <BrowserRouter>
        <StudioServicesProvider>
          <AppChrome>
            <AppHeader />
            <Suspense fallback={<div className="studio-route-loading" role="status">Loading Studio workspace…</div>}>
              <Routes>
                <Route path="/" element={<OverviewPage />} />
                <Route path="/queues" element={<QueueWorkerPage />} />
                <Route path="/services" element={<ServicesPage />} />
                <Route path="/services/:serviceId" element={<ServiceDetailPage />} />
                <Route path="/services/:serviceId/topology" element={<TopologyPage />} />
                <Route path="/services/:serviceId/load-tests" element={<LoadTestingPage />} />
                <Route path="/services/:serviceId/dlq" element={<DlqPage />} />
                <Route path="/failed-tasks" element={<FailedTasksPage />} />
                <Route path="/incidents" element={<IncidentInboxPage />} />
                <Route path="/recovery" element={<RecoveryCenterPage />} />
                <Route path="/recovery/:planId" element={<RecoveryCenterPage />} />
                <Route path="/tasks/search" element={<TaskSearchPage />} />
                <Route path="/tasks/:serviceId/:taskId" element={<TaskDetailPage />} />
                <Route path="/reliability" element={<ReliabilityPage />} />

                <Route path="/health" element={<HealthPage />} />
                <Route path="/access" element={<AccessPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </AppChrome>
        </StudioServicesProvider>
      </BrowserRouter>
    </StudioAuthProvider>
  );
}
