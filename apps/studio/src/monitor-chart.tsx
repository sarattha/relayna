import { useEffect, useRef, useState } from "react";
import type { StudioMetricSeries } from "./types";

export function MonitorCpuChart({
  series,
  from,
  to,
  selectedTime,
}: {
  series: StudioMetricSeries[];
  from: string;
  to: string;
  selectedTime: string;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1000);
  useEffect(() => {
    if (!frame.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(240, entry.contentRect.width)),
    );
    observer.observe(frame.current);
    return () => observer.disconnect();
  }, []);
  const cpu = series.filter((item) => item.metric === "cpu_usage");
  const samples = cpu
    .flatMap((item) => item.points)
    .filter(
      (point) =>
        point.value !== null &&
        Number.isFinite(point.value) &&
        Number.isFinite(Date.parse(point.timestamp)),
    );
  const start = Date.parse(from);
  const end = Date.parse(to);
  if (
    !samples.length ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    start >= end
  )
    return (
      <div ref={frame} className="monitor-cpu-chart">
        <p>No CPU samples reported in this window.</p>
      </div>
    );
  const maximum = Math.max(0.01, ...samples.map((point) => point.value!));
  const x = (time: number) =>
    48 + ((time - start) / (end - start)) * (width - 70);
  const y = (value: number) => 100 - (value / maximum) * 70;
  const selected = Date.parse(selectedTime);
  return (
    <div ref={frame} className="monitor-cpu-chart">
      <svg
        viewBox={`0 0 ${width} 140`}
        role="img"
        aria-label={`Pod CPU in cores${selectedTime ? "; selected event marked" : ""}`}
      >
        {[0, maximum / 2, maximum].map((value) => (
          <g key={value}>
            <line
              x1={48}
              x2={width - 22}
              y1={y(value)}
              y2={y(value)}
              stroke="var(--studio-border)"
            />
            <text
              x={4}
              y={y(value) + 4}
              fill="var(--studio-text-muted)"
              fontSize={11}
            >
              {value.toFixed(2)}
            </text>
          </g>
        ))}
        {(width < 500 ? [0, 2, 4] : [0, 1, 2, 3, 4]).map((index) => {
          const time = start + ((end - start) * index) / 4;
          return (
            <g key={time}>
              <line
                x1={x(time)}
                x2={x(time)}
                y1={30}
                y2={100}
                stroke="var(--studio-border)"
              />
              <text
                x={x(time)}
                y={122}
                textAnchor={
                  index === 0 ? "start" : index === 4 ? "end" : "middle"
                }
                fill="var(--studio-text-muted)"
                fontSize={11}
              >
                {new Date(time).toLocaleTimeString([], { hour12: false })}
              </text>
            </g>
          );
        })}
        {cpu.map((item, index) => (
          <path
            key={index}
            d={(() => {
              let segmentStart = true;
              return item.points
                .map((point) => {
                  const time = Date.parse(point.timestamp);
                  if (
                    point.value === null ||
                    !Number.isFinite(point.value) ||
                    !Number.isFinite(time)
                  ) {
                    segmentStart = true;
                    return "";
                  }
                  const command = segmentStart ? "M" : "L";
                  segmentStart = false;
                  return `${command}${x(time)},${y(point.value)}`;
                })
                .join(" ");
            })()}
            fill="none"
            stroke={
              [
                "var(--studio-secondary-strong)",
                "var(--studio-primary)",
                "var(--studio-success)",
              ][index % 3]
            }
            strokeWidth={2}
          />
        ))}
        {Number.isFinite(selected) && selected >= start && selected <= end && (
          <g aria-label="Selected event">
            <line
              x1={x(selected)}
              x2={x(selected)}
              y1={25}
              y2={100}
              stroke="var(--studio-danger)"
              strokeDasharray="3 3"
            />
            <circle
              cx={x(selected)}
              cy={25}
              r={3}
              fill="var(--studio-danger)"
            />
            <text
              x={x(selected)}
              y={14}
              textAnchor="middle"
              fill="var(--studio-danger)"
              fontSize={11}
            >
              Selected event
            </text>
          </g>
        )}
      </svg>
      <div className="monitor-cpu-legend">
        {cpu.map((item, index) => (
          <span key={index}>
            {item.labels.pod || item.labels.container || `Series ${index + 1}`}{" "}
            · cores
          </span>
        ))}
      </div>
    </div>
  );
}
