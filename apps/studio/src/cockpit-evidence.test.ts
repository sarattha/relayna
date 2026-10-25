import { describe, expect, it } from "vitest";
import { duration, isFresh, latestMetric } from "./cockpit-evidence";
import { cockpitFixture } from "./test/cockpit-fixtures";

const now = Date.now();
describe("cockpit evidence", () => {
  it("bounds freshness, including invalid, missing and future timestamps", () => {
    expect(isFresh(new Date(now).toISOString(), now)).toBe(true);
    expect(isFresh(new Date(now - 91_000).toISOString(), now)).toBe(false);
    expect(isFresh(new Date(now + 31_000).toISOString(), now)).toBe(false);
    expect(isFresh(undefined, now)).toBe(false);
    expect(isFresh("invalid", now)).toBe(false);
    expect(isFresh(new Date(now).toISOString().replace(/Z$/, ""), now)).toBe(
      false,
    );
    expect(
      isFresh(new Date(now).toISOString().replace(/Z$/, "+00:00"), now),
    ).toBe(true);
    expect(
      isFresh(new Date(now).toISOString().replace(/Z$/, "+0000"), now),
    ).toBe(true);
  });
  it("formats observed zero and durations without replacing missing values", () => {
    expect([
      duration(null),
      duration(Infinity),
      duration(0),
      duration(61),
      duration(3600),
      duration(86400),
    ]).toEqual(["Not reported", "Not reported", "0s", "2m", "1.0h", "1.0d"]);
  });
  it("uses the newest aggregate sample and preserves latest null/stale/ambiguous data", () => {
    const series = cockpitFixture(now).metrics!.series;
    expect(latestMetric(series, "active_tasks", now)).toBe(2);
    expect(latestMetric(series, "missing", now)).toBeNull();
    expect(
      latestMetric([...series, series[2]], "active_tasks", now),
    ).toBeNull();
    const sample = series[2];
    sample.points.push({
      timestamp: new Date(now + 1).toISOString(),
      value: null,
    });
    expect(latestMetric(series, "active_tasks", now)).toBeNull();
    sample.points.pop();
    expect(latestMetric(series, "active_tasks", now + 100_000)).toBeNull();
    sample.points = [];
    expect(latestMetric(series, "active_tasks", now)).toBeNull();
  });
});
