import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setStudioCsrfToken, updateStudioUser } from "./api";
import { StudioAuthProvider, useStudioAuth } from "./auth-context";

const fetchMock = vi.fn<typeof fetch>();

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function session(status: "pending" | "active" | "blocked" = "active", role: "admin" | "readonly" = "admin") {
  return {
    user: {
      user_id: "user-1",
      tenant_id: "tenant-1",
      object_id: "user-1",
      email: "user@example.test",
      display_name: "Studio User",
      role,
      status,
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    },
    csrf_token: "csrf-1",
  };
}

function SessionChild() {
  const auth = useStudioAuth();
  return <button onClick={() => void auth.signOut()}>{auth.isAdmin ? "Admin session" : "Read-only session"}</button>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  setStudioCsrfToken(null);
});

describe("StudioAuthProvider", () => {
  it("renders signed-out, pending, blocked, and active session states", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(response({ detail: "Studio authentication is required." }, 401));
    const signedOut = render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("heading", { name: "Sign in to Studio" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in with Microsoft Entra" })).toHaveAttribute("href", expect.stringContaining("/studio/auth/login"));
    signedOut.unmount();

    fetchMock
      .mockResolvedValueOnce(response(session("pending", "readonly")))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const pending = render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("heading", { name: "Access pending" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Studio" })).toBeInTheDocument();
    pending.unmount();

    fetchMock
      .mockResolvedValueOnce(response(session("blocked", "readonly")))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const blocked = render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("heading", { name: "Access blocked" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to Studio" })).toBeInTheDocument();
    blocked.unmount();

    fetchMock.mockResolvedValueOnce(response(session("active", "readonly")));
    render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("button", { name: "Read-only session" })).toBeInTheDocument();
  });

  it("sends CSRF on local logout and returns to signed out", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(null, { status: 204 }));
    render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Admin session" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in to Studio" })).toBeInTheDocument());
    const logoutCall = fetchMock.mock.calls.find(([input]) => String(input) === "/studio/auth/logout");
    expect(logoutCall?.[1]?.method).toBe("POST");
    expect(new Headers(logoutCall?.[1]?.headers).get("X-CSRF-Token")).toBe("csrf-1");
  });

  it("shows server permission feedback and refreshes the member state", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session()));
    render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("button", { name: "Admin session" })).toBeInTheDocument();

    window.dispatchEvent(
      new CustomEvent("relayna:api-error", {
        detail: { status: 403, input: "/studio/services", message: "Administrator access is required." },
      }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Administrator access is required.");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a recoverable authorization error", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockRejectedValueOnce(new Error("Identity provider unavailable")).mockResolvedValueOnce(response(session()));
    render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("heading", { name: "Authorization error" })).toBeInTheDocument();
    expect(screen.getByText("Identity provider unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Admin session" })).toBeInTheDocument();
  });

  it("renders an authorization callback error from the Studio URL", async () => {
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/?auth_error=Microsoft%20Entra%20cancelled%20sign-in.");
    fetchMock.mockResolvedValueOnce(response({ detail: "Studio authentication is required." }, 401));
    render(<StudioAuthProvider><SessionChild /></StudioAuthProvider>);
    expect(await screen.findByRole("heading", { name: "Authorization error" })).toBeInTheDocument();
    expect(screen.getByText("Microsoft Entra cancelled sign-in.")).toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });

  it("rejects auth context use outside the provider", () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const preventWindowError = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener("error", preventWindowError);
    expect(() => render(<SessionChild />)).toThrow("useStudioAuth must be used within StudioAuthProvider");
    window.removeEventListener("error", preventWindowError);
    errorSpy.mockRestore();
  });

  it("encodes member identities in access updates", async () => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(response(session().user));
    await updateStudioUser("tenant-1:user/one", { status: "active" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/studio/admin/users/tenant-1%3Auser%2Fone",
      expect.objectContaining({ method: "PATCH" }),
    );
  });
});
