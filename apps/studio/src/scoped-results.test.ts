import { describe, expect, it, vi } from "vitest";
import { scopedResults } from "./scoped-results";
import { requestJson, setStudioCsrfToken } from "./api";

describe("scoped task reads", () => {
  it("follows each service cursor and merges all pages without omissions", async () => {
    const rows: Record<string, string[]> = { prodA: ["9", "6", "3"], prodB: ["8", "7", "2", "1"] };
    const read = vi.fn(async (sid: string, cursor: string | null) => {
      const offset = Number(cursor || 0);
      return { items: rows[sid].slice(offset, offset + 2), next_cursor: offset + 2 < rows[sid].length ? String(offset + 2) : null };
    });
    const first = await scopedResults(Object.keys(rows), null, 3, read, String);
    const second = await scopedResults(Object.keys(rows), first.next_cursor, 3, read, String);
    const third = await scopedResults(Object.keys(rows), second.next_cursor, 3, read, String);
    expect([...first.items, ...second.items, ...third.items]).toEqual(["9", "8", "7", "6", "3", "2", "1"]);
    expect(third.next_cursor).toBeNull();
  });

  it("keeps successful results and identifies unavailable services", async () => {
    const response = await scopedResults(["available", "offline"], null, 10, async (sid) => {
      if (sid === "offline") throw new Error("Timed out");
      return { items: ["task"], next_cursor: null };
    }, String);
    expect(response.items).toEqual(["task"]);
    expect(response.errors).toEqual([{ service_id: "offline", detail: "Timed out" }]);
    expect(response.scanned_services).toEqual(["available"]);
  });

  it("coalesces simultaneous identical reads and forwards cancellation", async () => {
    let release!: (response: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { release = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const first = requestJson("/studio/test-coalescing");
      const second = requestJson("/studio/test-coalescing");
      expect(fetchMock).toHaveBeenCalledTimes(1);
      release(new Response(JSON.stringify({ ok: true })));
      expect(await first).toEqual({ ok: true });
      expect(await second).toEqual({ ok: true });
      const controller = new AbortController();
      controller.abort();
      const third = requestJson("/studio/test-cancel", { signal: controller.signal });
      expect((fetchMock.mock.calls as unknown as Array<[string, RequestInit]>)[1][1].signal?.aborted).toBe(true);
      release(new Response("{}"));
      await third;
    } finally { vi.unstubAllGlobals(); }
  });
  it("preserves empty cursor positions and rejects malformed scopes without hiding read errors", async () => {
    const read = vi.fn(async (sid: string, cursor: string | null) => {
      if (sid === "offline") throw "opaque";
      if (!cursor) return { items: [], next_cursor: "next" };
      return { items: ["found"], next_cursor: "next" };
    });
    const first = await scopedResults(["available", "offline"], null, 5, read, String);
    expect(first.items).toEqual([]); expect(first.errors).toEqual([{ service_id: "offline", detail: "Service read failed." }]);
    const next = await scopedResults(["available"], first.next_cursor, 5, read, String); expect(next.items).toEqual(["found"]); expect(next.next_cursor).toBeNull();
    await expect(scopedResults(["available"], "not-json", 5, read, String)).rejects.toThrow();
  });
  it("propagates cancellation that arrives after a request has started", async () => {
    let release!: (response: Response) => void;
    const fetch = vi.fn((_input: string, init: RequestInit) => new Promise<Response>((resolve) => { release = resolve; expect(init.signal?.aborted).toBe(false); }));
    vi.stubGlobal("fetch", fetch);
    try {
      const controller = new AbortController(); const pending = requestJson("/studio/later-cancel", { signal: controller.signal });
      controller.abort("superseded"); expect(fetch.mock.calls[0][1].signal?.aborted).toBe(true); expect(fetch.mock.calls[0][1].signal?.reason).toBe("superseded");
      release(new Response("{}")); await pending;
    } finally { vi.unstubAllGlobals(); }
  });
  it("ends empty service cursors rather than repeatedly loading the same page", async () => {
    const read = vi.fn(async () => ({ items: [], next_cursor: null }));
    const result = await scopedResults(["empty"], null, 5, read, String); expect(result.items).toEqual([]); expect(result.next_cursor).toBeNull(); expect(read).toHaveBeenCalledOnce();
  });
  it("aborts a hung provider request at the bounded request deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", (_input: string, init: RequestInit) => new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason))));
    try {
      const pending = requestJson("/studio/hung-provider"); const rejected = expect(pending).rejects.toThrow("Request timed out. Please retry.");
      await vi.advanceTimersByTimeAsync(20001); await rejected;
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
  });
});


describe("managed upload deadlines", () => {
  it("receives a signed upload reference after the normal 20-second deadline", async () => {
    vi.useFakeTimers();
    let resolve!: (response: Response) => void;
    const fetchMock = vi.fn((_input: string, _init: RequestInit) => new Promise<Response>((done) => { resolve = done; }));
    vi.stubGlobal("fetch", fetchMock);
    setStudioCsrfToken("synthetic-csrf");
    try {
      const body = new FormData(); body.set("file", new File(["fixture"], "fixture.txt"));
      const pending = requestJson("/studio/services/service%2Fid/load-tests/chamber/uploads", { method: "POST", body });
      const init = fetchMock.mock.calls[0][1];
      expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("synthetic-csrf");
      expect(init.body).toBe(body);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(init.signal?.aborted).toBe(false);
      resolve(new Response(JSON.stringify({ file: { pathToken: "signed-fixture" } })));
      await expect(pending).resolves.toEqual({ file: { pathToken: "signed-fixture" } });
      expect(vi.getTimerCount()).toBe(0);
    } finally { setStudioCsrfToken(null); vi.useRealTimers(); vi.unstubAllGlobals(); }
  });

  it("still ends a hung managed upload at the bounded five-minute deadline", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", (_input: string, init: RequestInit) => new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason))));
    try {
      const pending = requestJson("/studio/services/svc/load-tests/chamber/uploads", { method: "POST", body: new FormData() });
      const rejected = expect(pending).rejects.toThrow("Request timed out. Please retry.");
      await vi.advanceTimersByTimeAsync(300_001); await rejected;
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
  });

  it("honors external cancellation during an upload", async () => {
    vi.stubGlobal("fetch", (_input: string, init: RequestInit) => new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason))));
    try {
      const controller = new AbortController();
      const pending = requestJson("/studio/services/svc/load-tests/chamber/uploads", { method: "POST", body: new FormData(), signal: controller.signal });
      const rejected = expect(pending).rejects.toThrow("Upload cancelled");
      controller.abort(new Error("Upload cancelled")); await rejected;
    } finally { vi.unstubAllGlobals(); }
  });

  it.each([
    ["/studio/services/svc/load-tests/chamber/uploads", "POST", "{}"],
    ["/studio/services/svc/load-tests/chamber/uploads", "PUT", new FormData()],
    ["/studio/other-upload", "POST", new FormData()],
  ])("keeps ordinary request deadlines for %s %s", async (path, method, body) => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", (_input: string, init: RequestInit) => new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason))));
    try {
      const rejected = expect(requestJson(path, { method, body })).rejects.toThrow("Request timed out. Please retry.");
      await vi.advanceTimersByTimeAsync(20_001); await rejected;
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); }
  });
});
