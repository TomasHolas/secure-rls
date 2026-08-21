/**
 * The chart brick: renders a backend ChartSpec (the contract in apps/backend/analytics.py)
 * verbatim as hand-rolled SVG, dispatching on `kind`. Ported from the KB's chart bricks
 * (AreaTrend's measured-width SVG scaffolding and grid/axis chrome, BarTimeline's bar
 * register) — the KB uses no chart library, so neither does this (ADR 0006).
 */

import { useLayoutEffect, useRef, useState } from "react";

import { EmptyState } from "../layout/EmptyState";

export type ChartKind = "bar" | "line" | "histogram";

export interface ChartPoint {
  x: string;
  y: number;
}

/** The `plot` tool's payload, consumed exactly as analytics.py emits it. */
export interface ChartSpec {
  kind: ChartKind;
  title: string;
  x_label: string;
  y_label: string;
  data: ChartPoint[];
}

const PAD = { left: 62, right: 16, top: 14, bottom: 48 };
const FALLBACK_WIDTH = 640;
const MAX_X_TICKS = 8;
/** Histogram bins are a continuous axis, so they sit nearly flush; bar categories breathe. */
const GAP_RATIO = { bar: 0.3, histogram: 0.06 };
/** KB's .bar-fill caps a category column at 38px so a three-bar chart is not three slabs. */
const MAX_BAR_WIDTH = { bar: 38, histogram: Infinity };

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function Chart({ spec, height = 260 }: { spec: ChartSpec; height?: number }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);

  // Measured before the first paint: a frame at FALLBACK_WIDTH would reflow the plot under
  // a reader mid-stream, and the chart arrives exactly while tokens are still landing.
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => setWidth(el.getBoundingClientRect().width || FALLBACK_WIDTH);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (spec.data.length === 0) {
    return (
      <div className="chart" style={{ minHeight: height }}>
        <div className="chart-title">{spec.title}</div>
        <EmptyState icon="bar-chart">No data to plot.</EmptyState>
      </div>
    );
  }

  const values = spec.data.map((p) => p.y);
  // The domain always contains 0, so the bar baseline stays on the plot whatever the sign.
  const yMin = Math.min(0, ...values);
  const yMax = Math.max(0, ...values);
  const span = yMax - yMin || 1;
  const innerW = Math.max(width - PAD.left - PAD.right, 10);
  const innerH = Math.max(height - PAD.top - PAD.bottom, 10);
  const band = innerW / spec.data.length;

  const x = (i: number) => PAD.left + band * (i + 0.5);
  const y = (v: number) => PAD.top + innerH - (innerH * (v - yMin)) / span;

  const yTicks = [yMin, yMin + span / 2, yMin + span];
  const tickEvery = Math.ceil(spec.data.length / MAX_X_TICKS);

  return (
    <div ref={wrapRef} className="chart" style={{ minHeight: height }}>
      <div className="chart-title">{spec.title}</div>
      <svg width="100%" height={height} role="img" aria-label={spec.title}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} y1={y(v)} x2={PAD.left + innerW} y2={y(v)} className="chart-grid" />
            <text x={PAD.left - 8} y={y(v) + 3} textAnchor="end" className="chart-axis">
              {compact.format(v)}
            </text>
          </g>
        ))}
        {spec.data.map((p, i) =>
          i % tickEvery === 0 ? (
            <text
              key={p.x}
              x={x(i)}
              y={PAD.top + innerH + 18}
              textAnchor="middle"
              className="chart-axis"
            >
              {p.x}
            </text>
          ) : null,
        )}
        <text
          x={PAD.left + innerW / 2}
          y={height - 8}
          textAnchor="middle"
          className="chart-axis-title"
        >
          {spec.x_label}
        </text>
        <text
          transform={`translate(14 ${PAD.top + innerH / 2}) rotate(-90)`}
          textAnchor="middle"
          className="chart-axis-title"
        >
          {spec.y_label}
        </text>
        {spec.kind === "line" ? (
          <Line data={spec.data} x={x} y={y} baseline={y(0)} />
        ) : (
          <Bars
            data={spec.data}
            x={x}
            y={y}
            baseline={y(0)}
            width={Math.min(band * (1 - GAP_RATIO[spec.kind]), MAX_BAR_WIDTH[spec.kind])}
          />
        )}
      </svg>
    </div>
  );
}

type Scale = (n: number) => number;

function Bars({
  data,
  x,
  y,
  baseline,
  width,
}: {
  data: ChartPoint[];
  x: Scale;
  y: Scale;
  baseline: number;
  width: number;
}) {
  return (
    <>
      {data.map((p, i) => (
        <rect
          key={p.x}
          className="chart-bar"
          x={x(i) - width / 2}
          y={Math.min(y(p.y), baseline)}
          width={width}
          height={Math.max(1, Math.abs(baseline - y(p.y)))}
          rx={2}
        >
          <title>{`${p.x}: ${p.y}`}</title>
        </rect>
      ))}
    </>
  );
}

function Line({
  data,
  x,
  y,
  baseline,
}: {
  data: ChartPoint[];
  x: Scale;
  y: Scale;
  baseline: number;
}) {
  const line = data.map((p, i) => `${x(i)},${y(p.y)}`).join(" ");
  return (
    <>
      <polygon className="chart-area" points={`${x(0)},${baseline} ${line} ${x(data.length - 1)},${baseline}`} />
      <polyline className="chart-line" points={line} />
      {data.map((p, i) => (
        <circle key={p.x} className="chart-dot" cx={x(i)} cy={y(p.y)} r={2.5}>
          <title>{`${p.x}: ${p.y}`}</title>
        </circle>
      ))}
    </>
  );
}
