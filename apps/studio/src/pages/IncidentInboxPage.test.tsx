import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  IncidentDetail,
  IncidentInboxResponse,
  IncidentSummary,
} from "../types";
import { IncidentInboxPage } from "./IncidentInboxPage";

const mocks = vi.hoisted(() => ({
  fetchIncidentInbox: vi.fn(),
  fetchIncident: vi.fn(),
  updateIncident: vi.fn(),
  splitIncident: vi.fn(),
  mergeIncident: vi.fn(),
  useStudioAuth: vi.fn(),
}));
vi.mock("../api", () => mocks);
vi.mock("../auth-context", () => ({ useStudioAuth: mocks.useStudioAuth }));
vi.mock("../services-context", () => ({
  useStudioServices: () => ({ servicesById: new Map() }),
}));

const big: IncidentSummary = {
  grouping: "automatic",
  incident_id: "big",
  service_id: "payments",
  environment: "prod",
  dimensions: {
    signature_version: "1",
    queue_name: "payment-capture",
    stage: "capture",
    error_type: "TimeoutError",
    signature: "Capture <number> timed out",
  },
  status: "open",
  owner: null,
  revision: 2,
  merged_into: null,
  created_at: "2026-10-25T10:00:00Z",
  updated_at: "2026-10-25T10:00:00Z",
  failure_count: 380,
  affected_tasks: 380,
  missing_task_ids: 0,
  first_seen: "2026-10-25T10:00:00Z",
  last_seen: "2026-10-25T11:00:00Z",
  trend: {
    recent_hour: 380,
    preceding_hour: 0,
    unknown_timestamps: 0,
    outside_two_hours: 0,
  },
  representative: [],
};
const small: IncidentSummary = {
  ...big,
  incident_id: "small",
  failure_count: 8,
  dimensions: {
    ...big.dimensions,
    error_type: "ConnectionError",
    signature: "Connection reset",
  },
};
const coverage = [
  {
    service_id: "payments",
    environment: "prod",
    status: "complete" as const,
    observed_failures: 388,
    pages: 8,
    detail: null,
  },
];
const inbox: IncidentInboxResponse = {
  items: [big, small],
  coverage,
  total_incidents: 2,
  next_offset: null,
  complete: true,
  observed_failures: 388,
  scanned_at: "2026-10-25T12:00:00Z",
  count_scope: "bounded_live_scan",
};

function detail(item = big): IncidentDetail {
  const evidence = Array.from({ length: 3 }, (_, i) => ({
    failure_id: `f-${i}`,
    task_id: `task-${i}`,
    service_id: item.service_id,
    environment: item.environment,
    queue_name: "payment-capture",
    stage: "capture",
    error_type: item.dimensions.error_type,
    error_message: `Capture ${i} timed out`,
    failed_at: item.last_seen,
  }));
  return {
    ...item,
    failure_count: 3,
    affected_tasks: 3,
    representative: evidence.slice(0, 1),
    evidence,
    coverage,
    complete: true,
    scanned_at: inbox.scanned_at,
    count_scope: "bounded_live_scan",
    history: [],
    history_limit: 100,
  };
}

function RouteControls() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/incidents?environment=stage")}>
      Switch environment
    </button>
  );
}

function page(url = "/incidents?environment=prod") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <RouteControls />
      <IncidentInboxPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.useStudioAuth.mockReturnValue({ isAdmin: true });
  mocks.fetchIncidentInbox.mockResolvedValue(inbox);
  mocks.fetchIncident.mockImplementation(async (id) =>
    detail(id === "small" ? small : big),
  );
  mocks.updateIncident.mockResolvedValue({ incident_id: "big", revision: 3 });
  mocks.splitIncident.mockResolvedValue({ incident_id: "split", revision: 0 });
  mocks.mergeIncident.mockResolvedValue({ incident_id: "small", revision: 3 });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("Incident Inbox", () => {
  it("shows the largest cohort, discloses scan coverage, and opens task investigation", async () => {
    page();
    expect(
      await screen.findByRole("button", { name: /TimeoutError.*380/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/388 observed failures in the full scan/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Complete retained query/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /TimeoutError.*380/ }));
    expect(
      await screen.findByRole("heading", { name: "Representative evidence" }),
    ).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "Investigate task-0" });
    expect(links[0]).toHaveAttribute(
      "href",
      "/tasks/payments/task-0?view=monitor&layout=investigation&environment=prod",
    );
    expect(screen.getByText("Capture 0 timed out")).toBeInTheDocument();
    expect(mocks.fetchIncidentInbox).toHaveBeenCalledWith("prod", "active", 0);
  });

  it("saves owner, acknowledgement and a note with the displayed revision", async () => {
    page("/incidents?environment=prod&incident=big");
    fireEvent.change(await screen.findByLabelText("Incident owner"), {
      target: { value: "Payments team" },
    });
    fireEvent.change(screen.getByLabelText("Incident lifecycle status"), {
      target: { value: "acknowledged" },
    });
    fireEvent.change(screen.getByLabelText("Incident note"), {
      target: { value: "Investigating upstream timeout" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save incident" }));
    await waitFor(() =>
      expect(mocks.updateIncident).toHaveBeenCalledWith("big", {
        revision: 2,
        owner: "Payments team",
        status: "acknowledged",
        note: "Investigating upstream timeout",
      }),
    );
    expect(await screen.findByText(/Incident saved/)).toBeInTheDocument();
  });

  it("keeps inputs and shows a conflict without retrying a stale edit", async () => {
    mocks.updateIncident.mockRejectedValueOnce(
      new Error("Incident changed. Refresh before applying this action."),
    );
    page("/incidents?incident=big");
    fireEvent.change(await screen.findByLabelText("Incident owner"), {
      target: { value: "Team" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save incident" }));
    expect(await screen.findByText(/Incident changed/)).toBeInTheDocument();
    expect(screen.getByLabelText("Incident owner")).toHaveValue("Team");
    expect(mocks.updateIncident).toHaveBeenCalledTimes(1);
  });

  it("splits only selected observed records and navigates to the new incident", async () => {
    page("/incidents?incident=big");
    const box = await screen.findByLabelText("Select failure f-0");
    expect(
      screen.getByRole("button", { name: "Split selected failures" }),
    ).toBeDisabled();
    fireEvent.click(box);
    fireEvent.click(
      screen.getByRole("button", { name: "Split selected failures" }),
    );
    await waitFor(() =>
      expect(mocks.splitIncident).toHaveBeenCalledWith("big", {
        revision: 2,
        failure_ids: ["f-0"],
        note: "",
      }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncident).toHaveBeenCalledWith("split"),
    );
  });

  it("merges into a same-service cohort with revisions for both incidents", async () => {
    page("/incidents?incident=big");
    fireEvent.change(await screen.findByLabelText("Merge target"), {
      target: { value: "small" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Merge incident" }));
    await waitFor(() =>
      expect(mocks.mergeIncident).toHaveBeenCalledWith("big", {
        revision: 2,
        target_id: "small",
        target_revision: 2,
        note: "",
      }),
    );
  });

  it("hides lifecycle and correction controls for readonly members", async () => {
    mocks.useStudioAuth.mockReturnValue({ isAdmin: false });
    page("/incidents?incident=big");
    await screen.findByRole("heading", { name: "Representative evidence" });
    expect(
      screen.queryByRole("button", { name: "Save incident" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Merge target")).not.toBeInTheDocument();
  });

  it("preserves unavailable and empty states without claiming no failures", async () => {
    mocks.fetchIncidentInbox.mockResolvedValue({
      ...inbox,
      items: [],
      complete: false,
      coverage: [
        {
          ...coverage[0],
          status: "unavailable",
          detail: "Upstream unreachable",
        },
      ],
    });
    page();
    expect(
      await screen.findByText("No incidents found in available evidence"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Available counts are lower bounds/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Upstream unreachable/)).toBeInTheDocument();
  });

  it("keeps a prior scan on refresh failure and marks it stale", async () => {
    page();
    await screen.findByRole("button", { name: /TimeoutError.*380/ });
    mocks.fetchIncidentInbox.mockRejectedValueOnce(new Error("Scan failed"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
    expect(
      await screen.findByText(/Scan failed.*previous scan/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /TimeoutError.*380/ }),
    ).toBeInTheDocument();
  });

  it("ignores late list responses after switching environments", async () => {
    let resolveFirst!: (value: IncidentInboxResponse) => void;
    mocks.fetchIncidentInbox.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    page();
    fireEvent.click(screen.getByRole("button", { name: "Switch environment" }));
    await waitFor(() =>
      expect(mocks.fetchIncidentInbox).toHaveBeenCalledWith(
        "stage",
        "active",
        0,
      ),
    );
    mocks.fetchIncidentInbox.mockResolvedValue({ ...inbox, items: [] });
    await act(async () =>
      resolveFirst({
        ...inbox,
        items: [
          {
            ...big,
            dimensions: { ...big.dimensions, error_type: "LateProdError" },
          },
        ],
      }),
    );
    expect(
      screen.queryByRole("button", { name: /LateProdError/ }),
    ).not.toBeInTheDocument();
  });

  it("ignores late details after selecting another cohort", async () => {
    let resolveFirst!: (value: IncidentDetail) => void;
    mocks.fetchIncident.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFirst = resolve;
        }),
    );
    page("/incidents?incident=big");
    fireEvent.click(
      await screen.findByRole("button", { name: /ConnectionError.*8/ }),
    );
    const region = screen.getByLabelText("Incident evidence");
    expect(
      await within(region).findByRole("heading", { name: "ConnectionError" }),
    ).toBeInTheDocument();
    await act(async () => resolveFirst(detail()));
    expect(
      within(region).queryByRole("heading", { name: "TimeoutError" }),
    ).not.toBeInTheDocument();
  });
});

describe("Incident Inbox retained state and navigation", () => {
  it("pages cohorts and resets paging when the status filter changes", async () => {
    mocks.fetchIncidentInbox.mockImplementation(
      async (_env, _status, offset) => ({
        ...inbox,
        items: offset ? [small] : [big],
        total_incidents: 101,
        next_offset: offset ? null : 100,
      }),
    );
    page();
    fireEvent.click(
      await screen.findByRole("button", { name: "Next cohorts" }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncidentInbox).toHaveBeenCalledWith(
        "prod",
        "active",
        100,
      ),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Previous cohorts" }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncidentInbox).toHaveBeenLastCalledWith(
        "prod",
        "active",
        0,
      ),
    );
    fireEvent.change(screen.getByLabelText("Incident status filter"), {
      target: { value: "resolved" },
    });
    await waitFor(() =>
      expect(mocks.fetchIncidentInbox).toHaveBeenLastCalledWith(
        "prod",
        "resolved",
        0,
      ),
    );
  });

  it("shows retained state and linked history without implying that absent evidence is resolved", async () => {
    mocks.fetchIncidentInbox.mockResolvedValue({
      ...inbox,
      items: [
        { ...big, failure_count: 0, owner: "Payments", grouping: "manual" },
      ],
    });
    mocks.fetchIncident.mockResolvedValue({
      ...detail(),
      failure_count: 0,
      evidence: [],
      representative: [],
      owner: "Payments",
      status: "acknowledged",
      grouping: "manual",
      coverage: [],
      history: [
        {
          id: 1,
          action: "updated",
          occurred_at: inbox.scanned_at,
          actor_user_id: "Operator",
          details: {
            note: "Owner assigned",
            before: { owner: null },
            after: { owner: "Payments" },
          },
        },
        {
          id: 2,
          action: "merged_from",
          occurred_at: inbox.scanned_at,
          actor_user_id: null,
          details: { source_id: "small", target_id: "big", note: "" },
        },
        {
          id: 3,
          action: "split",
          occurred_at: inbox.scanned_at,
          actor_user_id: "Operator",
          details: { source_id: "big", target_id: "split", note: 123 },
        },
      ],
    });
    page("/incidents?incident=big");
    expect(await screen.findByText("Owner assigned")).toBeInTheDocument();
    expect(
      screen.getByText(/No matching failures were observed/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Grouping anchor:/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "No representative evidence is available from this scan.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No matching evidence in this scan; state retained/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Incident lifecycle status")).toHaveValue(
      "acknowledged",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open source incident" }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncident).toHaveBeenCalledWith("small"),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Open target incident" }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncident).toHaveBeenCalledWith("split"),
    );
  });

  it("keeps a merged source readable and opens its target", async () => {
    mocks.fetchIncident.mockResolvedValueOnce({
      ...detail(),
      merged_into: "small",
      evidence: [],
      representative: [],
    });
    page("/incidents?incident=big");
    fireEvent.click(
      await screen.findByRole("button", { name: "Open target incident" }),
    );
    await waitFor(() =>
      expect(mocks.fetchIncident).toHaveBeenCalledWith("small"),
    );
  });

  it("discloses records without task IDs, timestamps, error text, or queues", async () => {
    const value = detail();
    value.evidence = [
      {
        ...value.evidence[0],
        task_id: null,
        error_type: null,
        error_message: null,
        queue_name: null,
        failed_at: null,
      },
    ];
    value.representative = value.evidence;
    value.missing_task_ids = 1;
    mocks.fetchIncident.mockResolvedValue(value);
    page("/incidents?incident=big");
    expect(await screen.findByText("Unknown error")).toBeInTheDocument();
    expect(screen.getByText("No error message available.")).toBeInTheDocument();
    expect(screen.getAllByText("No task ID available")).toHaveLength(2);
    expect(
      screen.getByText(/excluded from affected-task counts/),
    ).toBeInTheDocument();
  });

  it("recovers from initial read errors into a complete empty environment", async () => {
    mocks.fetchIncidentInbox.mockRejectedValueOnce("unavailable");
    mocks.fetchIncident.mockRejectedValueOnce("unavailable");
    page("/incidents?incident=big");
    expect(
      await screen.findByText("Unable to load Incident Inbox."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("Unable to load incident evidence."),
    ).toBeInTheDocument();
    mocks.fetchIncidentInbox.mockResolvedValue({
      ...inbox,
      items: [],
      coverage: [],
      observed_failures: 0,
      total_incidents: 0,
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh evidence" }));
    expect(
      await screen.findByText("No incidents in this view"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No enabled services in this environment."),
    ).toBeInTheDocument();
  });

  it("rejects a detail outside the selected environment", async () => {
    mocks.fetchIncident.mockResolvedValue({
      ...detail(),
      environment: "staging",
    });
    page("/incidents?environment=prod&incident=big");
    expect(
      await screen.findByText(
        "This incident is outside the selected environment.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Incident owner")).not.toBeInTheDocument();
  });

  it.each([true, false])(
    "ignores late mutation completion after an environment change (success=%s)",
    async (success) => {
      let finish!: () => void;
      mocks.updateIncident.mockImplementationOnce(
        () =>
          new Promise((resolve, reject) => {
            finish = () =>
              success
                ? resolve({ incident_id: "big", revision: 3 })
                : reject(new Error("Old mutation failed"));
          }),
      );
      page("/incidents?environment=prod&incident=big");
      fireEvent.change(await screen.findByLabelText("Incident owner"), {
        target: { value: "Team" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save incident" }));
      fireEvent.click(
        screen.getByRole("button", { name: "Switch environment" }),
      );
      await act(async () => finish());
      expect(
        screen.queryByText(/Incident saved|Old mutation failed/),
      ).not.toBeInTheDocument();
      expect(mocks.fetchIncident).toHaveBeenCalledTimes(1);
    },
  );

  it("unassigns an owner and resolves once on duplicate submissions", async () => {
    let finish!: (value: unknown) => void;
    mocks.fetchIncident.mockResolvedValue({ ...detail(), owner: "Team" });
    mocks.updateIncident.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    page("/incidents?incident=big");
    const owner = await screen.findByLabelText("Incident owner");
    fireEvent.change(owner, { target: { value: " " } });
    fireEvent.change(screen.getByLabelText("Incident lifecycle status"), {
      target: { value: "resolved" },
    });
    fireEvent.submit(owner.closest("form")!);
    fireEvent.submit(owner.closest("form")!);
    expect(mocks.updateIncident).toHaveBeenCalledTimes(1);
    expect(mocks.updateIncident).toHaveBeenCalledWith("big", {
      revision: 2,
      owner: null,
      status: "resolved",
      note: "",
    });
    await act(async () => finish({ incident_id: "big", revision: 3 }));
  });

  it("lets operators cancel corrections and uncheck a split selection", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    page("/incidents?incident=big");
    const box = await screen.findByLabelText("Select failure f-0");
    fireEvent.click(box);
    fireEvent.click(
      screen.getByRole("button", { name: "Split selected failures" }),
    );
    expect(mocks.splitIncident).not.toHaveBeenCalled();
    fireEvent.click(box);
    expect(
      screen.getByRole("button", { name: "Split selected failures" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Merge target"), {
      target: { value: "small" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Merge incident" }));
    expect(mocks.mergeIncident).not.toHaveBeenCalled();
  });
});

it("preserves a status filter changed while an incident save is pending", async () => {
  let finish!: (value: unknown) => void;
  mocks.updateIncident.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  page("/incidents?environment=prod&incident=big");
  fireEvent.change(await screen.findByLabelText("Incident owner"), {
    target: { value: "Team" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save incident" }));
  fireEvent.change(screen.getByLabelText("Incident status filter"), {
    target: { value: "resolved" },
  });
  await act(async () => finish({ incident_id: "big", revision: 3 }));
  expect(screen.getByLabelText("Incident status filter")).toHaveValue(
    "resolved",
  );
  await waitFor(() =>
    expect(mocks.fetchIncidentInbox).toHaveBeenLastCalledWith(
      "prod",
      "resolved",
      0,
    ),
  );
});
