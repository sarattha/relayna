import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { StudioServicesProvider } from "./services-context";
import { AppChrome, AppHeader } from "./ui";

const DlqPage = lazy(() => import("./pages/DlqPage").then((module) => ({ default: module.DlqPage })));
const FailedTasksPage = lazy(() =>
  import("./pages/FailedTasksPage").then((module) => ({ default: module.FailedTasksPage })),
);
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

export function App() {
  return (
    <BrowserRouter>
      <StudioServicesProvider>
        <AppChrome>
          <AppHeader />
          <Suspense fallback={<div className="studio-route-loading" role="status">Loading Studio workspace…</div>}>
            <Routes>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/services" element={<ServicesPage />} />
              <Route path="/services/:serviceId" element={<ServiceDetailPage />} />
              <Route path="/services/:serviceId/topology" element={<TopologyPage />} />
              <Route path="/services/:serviceId/dlq" element={<DlqPage />} />
              <Route path="/failed-tasks" element={<FailedTasksPage />} />
              <Route path="/tasks/search" element={<TaskSearchPage />} />
              <Route path="/tasks/:serviceId/:taskId" element={<TaskDetailPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </AppChrome>
      </StudioServicesProvider>
    </BrowserRouter>
  );
}
