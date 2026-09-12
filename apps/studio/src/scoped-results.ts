// Client-side federation keeps environment filtering within existing service APIs.
// Each cursor stores positions, never retained task payloads.
export async function scopedResults<T>(
  serviceIds: string[], cursor: string | null | undefined, limit: number,
  fetchPage: (serviceId: string, cursor: string | null) => Promise<{ items: T[]; next_cursor?: string | null }>,
  order: (item: T) => string,
) {
  type Position = { cursor: string | null; skip: number; done?: boolean };
  const positions: Record<string, Position> = cursor ? JSON.parse(decodeURIComponent(cursor)) : {};
  const pages = new Map<string, T[]>();
  const next = new Map<string, string | null>();
  const errors: Array<{ service_id: string; detail: string }> = [];
  const scanned: string[] = [];
  async function load(sid: string) {
    const position = positions[sid] ||= { cursor: null, skip: 0 };
    if (position.done) return;
    try {
      const payload = await fetchPage(sid, position.cursor);
      pages.set(sid, payload.items.slice(position.skip));
      next.set(sid, payload.next_cursor || null);
      if (!scanned.includes(sid)) scanned.push(sid);
      if (!pages.get(sid)?.length) {
        if (payload.next_cursor && payload.next_cursor !== position.cursor) positions[sid] = { cursor: payload.next_cursor, skip: 0 };
        else position.done = true;
      }
    } catch (error) { errors.push({ service_id: sid, detail: error instanceof Error ? error.message : "Service read failed." }); }
  }
  // Read admission is bounded by requestJson; this also bounds the queued work.
  for (let offset = 0; offset < serviceIds.length; offset += 4) await Promise.all(serviceIds.slice(offset, offset + 4).map(load));
  const items: T[] = [];
  while (items.length < limit) {
    const available = [...pages.entries()].filter(([, rows]) => rows.length).sort(([a, left], [b, right]) => order(right[0]).localeCompare(order(left[0])) || b.localeCompare(a));
    if (!available.length) break;
    const [sid, rows] = available[0];
    items.push(rows.shift()!); positions[sid].skip++;
    if (!rows.length) {
      const following = next.get(sid);
      if (following && following !== positions[sid].cursor) {
        positions[sid] = { cursor: following, skip: 0 };
        if (items.length < limit) await load(sid);
      } else positions[sid].done = true;
    }
  }
  const more = serviceIds.some((sid) => !positions[sid]?.done && !errors.some((error) => error.service_id === sid));
  return { count: items.length, items, next_cursor: more ? encodeURIComponent(JSON.stringify(positions)) : null, errors, scanned_services: scanned };
}
