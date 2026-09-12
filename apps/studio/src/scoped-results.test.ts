import { describe, expect, it, vi } from "vitest";
import { scopedResults } from "./scoped-results";
import { requestJson } from "./api";

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
});
