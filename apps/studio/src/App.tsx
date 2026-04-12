import { ReactFlowProvider } from "@xyflow/react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { DlqPage } from "./pages/DlqPage";
import { ServiceDetailPage } from "./pages/ServiceDetailPage";
import { ServicesPage } from "./pages/ServicesPage";
import { TaskDetailPage } from "./pages/TaskDetailPage";
import { TaskSearchPage } from "./pages/TaskSearchPage";
import { TopologyPage } from "./pages/TopologyPage";
import { StudioServicesProvider } from "./services-context";
import { AppChrome, AppHeader } from "./ui";

export function App() {
  return (
    <BrowserRouter>
      <ReactFlowProvider>
        <StudioServicesProvider>
          <AppChrome>
            <AppHeader />
            <Routes>
              <Route path="/" element={<Navigate to="/services" replace />} />
              <Route path="/services" element={<ServicesPage />} />
              <Route path="/services/:serviceId" element={<ServiceDetailPage />} />
              <Route path="/services/:serviceId/topology" element={<TopologyPage />} />
              <Route path="/services/:serviceId/dlq" element={<DlqPage />} />
              <Route path="/tasks/search" element={<TaskSearchPage />} />
              <Route path="/tasks/:serviceId/:taskId" element={<TaskDetailPage />} />
              <Route path="*" element={<Navigate to="/services" replace />} />
            </Routes>
          </AppChrome>
        </StudioServicesProvider>
      </ReactFlowProvider>
    </BrowserRouter>
  );
}
