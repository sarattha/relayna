import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StudioMember } from "../types";
import { AccessPage } from "./AccessPage";

const mocks = vi.hoisted(() => ({
  listStudioUsers: vi.fn(),
  updateStudioUser: vi.fn(),
  useStudioAuth: vi.fn(),
}));

vi.mock("../api", () => ({
  listStudioUsers: mocks.listStudioUsers,
  updateStudioUser: mocks.updateStudioUser,
}));

vi.mock("../auth-context", () => ({ useStudioAuth: mocks.useStudioAuth }));

const admin: StudioMember = {
  user_id: "tenant-1:admin-oid",
  tenant_id: "tenant-1",
  object_id: "admin-oid",
  email: "admin@example.test",
  display_name: "Studio Admin",
  role: "admin",
  status: "active",
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  last_sign_in_at: "2026-08-18T01:00:00Z",
};

const pending: StudioMember = {
  ...admin,
  user_id: "tenant-1:pending-oid",
  object_id: "pending-oid",
  email: "pending@example.test",
  display_name: "Pending User",
  role: "readonly",
  status: "pending",
};

beforeEach(() => {
  mocks.listStudioUsers.mockReset();
  mocks.updateStudioUser.mockReset();
  mocks.useStudioAuth.mockReturnValue({ user: admin, isAdmin: true, signOut: vi.fn() });
});

describe("AccessPage", () => {
  it("lists members, protects self controls, and applies role and status updates", async () => {
    mocks.listStudioUsers.mockResolvedValue({ count: 2, users: [admin, pending] });
    mocks.updateStudioUser.mockImplementation(
      async (_userId: string, update: Partial<Pick<StudioMember, "role" | "status">>) => ({
        ...pending,
        ...update,
      }),
    );
    render(<AccessPage />);

    expect(await screen.findByText("Pending User")).toBeInTheDocument();
    const selects = screen.getAllByRole("combobox");
    expect(selects[0]).toBeDisabled();
    expect(selects[1]).toBeDisabled();
    fireEvent.change(selects[2], { target: { value: "admin" } });
    await waitFor(() => expect(mocks.updateStudioUser).toHaveBeenCalledWith(pending.user_id, { role: "admin" }));
    fireEvent.change(selects[3], { target: { value: "active" } });
    await waitFor(() => expect(mocks.updateStudioUser).toHaveBeenCalledWith(pending.user_id, { status: "active" }));
    fireEvent.click(screen.getAllByRole("button", { name: "Refresh" })[1]);
    await waitFor(() => expect(mocks.listStudioUsers).toHaveBeenCalledTimes(2));
  });

  it("shows load and update failures", async () => {
    mocks.listStudioUsers.mockRejectedValueOnce(new Error("Member list failed"));
    const first = render(<AccessPage />);
    expect(await screen.findByText("Member list failed")).toBeInTheDocument();
    first.unmount();

    mocks.listStudioUsers.mockResolvedValueOnce({ count: 1, users: [pending] });
    mocks.updateStudioUser.mockRejectedValueOnce(new Error("Update failed"));
    render(<AccessPage />);
    fireEvent.change((await screen.findAllByRole("combobox"))[0], { target: { value: "admin" } });
    expect(await screen.findByText("Update failed")).toBeInTheDocument();
  });

  it("denies the page when the current member is not an administrator", () => {
    mocks.useStudioAuth.mockReturnValue({ user: pending, isAdmin: false, signOut: vi.fn() });
    mocks.listStudioUsers.mockResolvedValue({ count: 0, users: [] });
    render(<AccessPage />);
    expect(screen.getByText("Studio administrator access is required.")).toBeInTheDocument();
  });
});
