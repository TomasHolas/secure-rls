/**
 * The chart brick: renders a backend ChartSpec (the contract in apps/backend/analytics.py)
 * verbatim as hand-rolled SVG, dispatching on `kind`. Ported from the KB's chart bricks
 * (AreaTrend's measured-width SVG scaffolding and grid/axis chrome, BarTimeline's bar
 * register) — the KB uses no chart library, so neither does this (ADR 0006).
 *
 * Every number it prints — axis tick, bin edge, hover value — goes through the one formatter
 * in `lib/format.ts`; the brick never builds a number's text itself.
 */

import { useLayoutEffect, useRef, useState } from "react";

import { formatNumber, formatRange } from "../../lib/format";
import { EmptyState } from "../layout/EmptyState";

export type ChartKind = "bar" | "line" | "grouped_bar" | "histogram" | "scatter" | "box";

/** One plotted point; which keys beyond `y` it carries is fixed per kind by the backend. */
export interface ChartPoint {
  y: number;
  x?: string;
  series?: string;
  x_value?: number;
  x_low?: number;
  x_high?: number;
  low?: number;
  q1?: number;
  q3?: number;
  high?: number;
}

/** The `plot` tool's payload, consumed exactly as analytics.py emits it. */
export interface ChartSpec {
  kind: ChartKind;
  title: string;
  x_label: string;
  y_label: string;
  series_label?: string;
  data: ChartPoint[];
}

const PAD = { left: 62, right: 16, top: 14, bottom: 48 };
const FALLBACK_WIDTH = 640;
const MAX_X_TICKS = 8;
const LABEL_DROP = 18;
const SCATTER_RADIUS = 2.5;
/**
 * How much of a band a mark fills, and its ceiling: histogram bins sit nearly flush on their
 * continuous axis, categories breathe, and KB's .bar-fill caps a column at 38px so a
 * three-bar chart is not three slabs.
 */
const BAND_FILL: Record<string, { gap: number; max: number }> = {
  bar: { gap: 0.3, max: 38 },
  histogram: { gap: 0.06, max: Infinity },
  grouped_bar: { gap: 0.2, max: 38 },
  box: { gap: 0.45, max: 46 },
};
/** Bars and areas are read against zero; a point cloud and a box against their own spread. */
const ZERO_BASED: ChartKind[] = ["bar", "line", "grouped_bar", "histogram"];
/** tokens.css ships --chart-1..--chart-10, the KB's colorblind-safe categorical palette. */
const SERIES_COLORS = 10;

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

  const innerW = Math.max(width - PAD.left - PAD.right, 10);
  const innerH = Math.max(height - PAD.top - PAD.bottom, 10);

  // A box reaches past its median to its quartiles and whiskers, so the axis spans those too.
  const reach = spec.data.flatMap((p) => [
    p.y,
    p.low ?? p.y,
    p.high ?? p.y,
    p.q1 ?? p.y,
    p.q3 ?? p.y,
  ]);
  const zeroBased = ZERO_BASED.includes(spec.kind);
  const yMin = Math.min(...reach, zeroBased ? 0 : Infinity);
  const yMax = Math.max(...reach, zeroBased ? 0 : -Infinity);
  const span = yMax - yMin || 1;
  const y = (v: number) => PAD.top + innerH - (innerH * (v - yMin)) / span;
  const yTicks = [yMin, yMin + span / 2, yMin + span];

  const categories = [...new Set(spec.data.map((p) => p.x ?? ""))];
  const series = [...new Set(spec.data.map((p) => p.series ?? ""))];
  const numericX = scatterScale(spec, innerW);

  const bands = tickLabels(spec);
  const marks = pointLabels(spec);
  const band = innerW / Math.max(bands.length, 1);
  const fill = BAND_FILL[spec.kind] ?? BAND_FILL.bar;
  // A grouped band holds one bar per series, so its ceiling scales with how many there are.
  const slots = spec.kind === "grouped_bar" ? series.length : 1;
  const markWidth = Math.min(band * (1 - fill.gap), fill.max * slots);
  const x = (i: number) => PAD.left + band * (i + 0.5);
  const tickEvery = Math.ceil(Math.max(bands.length, 1) / MAX_X_TICKS);
  // A bin's tick names its lower edge, so it sits on the boundary rather than mid-bar.
  const onBoundary = spec.kind === "histogram";

  return (
    <div ref={wrapRef} className="chart" style={{ minHeight: height }}>
      <div className="chart-title">{spec.title}</div>
      <svg width="100%" height={height} role="img" aria-label={spec.title}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} y1={y(v)} x2={PAD.left + innerW} y2={y(v)} className="chart-grid" />
            <text x={PAD.left - 8} y={y(v) + 3} textAnchor="end" className="chart-axis">
              {formatNumber(v)}
            </text>
          </g>
        ))}
        {numericX
          ? numericX.ticks.map((v) => (
              <text
                key={v}
                x={numericX.scale(v)}
                y={PAD.top + innerH + LABEL_DROP}
                textAnchor="middle"
                className="chart-axis"
              >
                {formatNumber(v)}
              </text>
            ))
          : bands.map((label, index) =>
              index % tickEvery === 0 ? (
                <text
                  key={index}
                  x={onBoundary ? x(index) - band / 2 : x(index)}
                  y={PAD.top + innerH + LABEL_DROP}
                  textAnchor="middle"
                  className="chart-axis"
                >
                  {label}
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
          <Line data={spec.data} labels={marks} x={x} y={y} baseline={y(0)} />
        ) : spec.kind === "scatter" && numericX ? (
          <Points
            data={spec.data}
            axes={{ x: spec.x_label, y: spec.y_label }}
            x={numericX.scale}
            y={y}
          />
        ) : spec.kind === "box" ? (
          <Boxes data={spec.data} x={x} y={y} width={markWidth} />
        ) : spec.kind === "grouped_bar" ? (
          <GroupedBars
            data={spec.data}
            categories={categories}
            series={series}
            x={x}
            y={y}
            baseline={y(0)}
            width={markWidth / slots}
          />
        ) : (
          <Bars data={spec.data} labels={marks} x={x} y={y} baseline={y(0)} width={markWidth} />
        )}
      </svg>
      {spec.kind === "grouped_bar" ? <Legend label={spec.series_label} series={series} /> : null}
    </div>
  );
}

type Scale = (n: number) => number;

/** The label under each band: a category, a year, or a histogram bin's lower edge. */
function tickLabels(spec: ChartSpec): string[] {
  if (spec.kind === "scatter") return [];
  if (spec.kind === "histogram") return spec.data.map((p) => formatNumber(p.x_low ?? 0));
  if (spec.kind === "grouped_bar") return [...new Set(spec.data.map((p) => p.x ?? ""))];
  return spec.data.map((p) => p.x ?? "");
}

/** What a mark calls itself in its hover title: a bin names its whole range, not one edge. */
function pointLabels(spec: ChartSpec): string[] {
  if (spec.kind !== "histogram") return spec.data.map((p) => p.x ?? "");
  return spec.data.map((p) => formatRange(p.x_low ?? 0, p.x_high ?? 0));
}

/** The linear x axis a scatter needs, with the three ticks that label it. */
function scatterScale(spec: ChartSpec, innerW: number) {
  if (spec.kind !== "scatter") return null;
  const values = spec.data.map((p) => p.x_value ?? 0);
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  return {
    scale: (v: number) => PAD.left + (innerW * (v - min)) / span,
    ticks: [min, min + span / 2, min + span],
  };
}

/** One series' colour: the token palette, cycled so any series count stays distinguishable. */
function seriesColor(index: number): string {
  return `var(--chart-${(index % SERIES_COLORS) + 1})`;
}

function Legend({ label, series }: { label?: string; series: string[] }) {
  return (
    <div className="chart-legend">
      {label ? <span className="chart-legend-label">{label}</span> : null}
      {series.map((name, index) => (
        <span className="chart-legend-item" key={name}>
          <span className="chart-swatch" style={{ background: seriesColor(index) }} />
          {name}
        </span>
      ))}
    </div>
  );
}

function Bars({
  data,
  labels,
  x,
  y,
  baseline,
  width,
}: {
  data: ChartPoint[];
  labels: string[];
  x: Scale;
  y: Scale;
  baseline: number;
  width: number;
}) {
  return (
    <>
      {data.map((p, i) => (
        <rect
          key={i}
          className="chart-bar"
          x={x(i) - width / 2}
          y={Math.min(y(p.y), baseline)}
          width={width}
          height={Math.max(1, Math.abs(baseline - y(p.y)))}
          rx={2}
        >
          <title>{`${labels[i]}: ${formatNumber(p.y)}`}</title>
        </rect>
      ))}
    </>
  );
}

function GroupedBars({
  data,
  categories,
  series,
  x,
  y,
  baseline,
  width,
}: {
  data: ChartPoint[];
  categories: string[];
  series: string[];
  x: Scale;
  y: Scale;
  baseline: number;
  width: number;
}) {
  return (
    <>
      {data.map((p, i) => {
        const slot = series.indexOf(p.series ?? "");
        const centre = x(categories.indexOf(p.x ?? "")) + (slot - (series.length - 1) / 2) * width;
        return (
          <rect
            key={i}
            className="chart-series"
            style={{ fill: seriesColor(slot) }}
            x={centre - width / 2}
            y={Math.min(y(p.y), baseline)}
            width={width}
            height={Math.max(1, Math.abs(baseline - y(p.y)))}
            rx={2}
          >
            <title>{`${p.x} / ${p.series}: ${formatNumber(p.y)}`}</title>
          </rect>
        );
      })}
    </>
  );
}

function Boxes({
  data,
  x,
  y,
  width,
}: {
  data: ChartPoint[];
  x: Scale;
  y: Scale;
  width: number;
}) {
  return (
    <>
      {data.map((p, i) => {
        const centre = x(i);
        const cap = width / 4;
        const quartileLow = y(p.q1 ?? p.y);
        const quartileHigh = y(p.q3 ?? p.y);
        const whiskerLow = y(p.low ?? p.y);
        const whiskerHigh = y(p.high ?? p.y);
        return (
          <g key={i}>
            <line
              className="chart-whisker"
              x1={centre}
              y1={whiskerHigh}
              x2={centre}
              y2={whiskerLow}
            />
            <line
              className="chart-whisker"
              x1={centre - cap}
              y1={whiskerHigh}
              x2={centre + cap}
              y2={whiskerHigh}
            />
            <line
              className="chart-whisker"
              x1={centre - cap}
              y1={whiskerLow}
              x2={centre + cap}
              y2={whiskerLow}
            />
            <rect
              className="chart-box"
              x={centre - width / 2}
              y={quartileHigh}
              width={width}
              height={Math.max(1, quartileLow - quartileHigh)}
              rx={2}
            >
              <title>
                {`${p.x}: median ${formatNumber(p.y)}, quartiles ` +
                  `${formatRange(p.q1 ?? p.y, p.q3 ?? p.y)}, whiskers ` +
                  `${formatRange(p.low ?? p.y, p.high ?? p.y)}`}
              </title>
            </rect>
            <line
              className="chart-median"
              x1={centre - width / 2}
              y1={y(p.y)}
              x2={centre + width / 2}
              y2={y(p.y)}
            />
          </g>
        );
      })}
    </>
  );
}

function Points({
  data,
  axes,
  x,
  y,
}: {
  data: ChartPoint[];
  axes: { x: string; y: string };
  x: Scale;
  y: Scale;
}) {
  return (
    <>
      {data.map((p, i) => (
        <circle
          key={i}
          className="chart-point"
          cx={x(p.x_value ?? 0)}
          cy={y(p.y)}
          r={SCATTER_RADIUS}
        >
          <title>
            {`${p.x}: ${axes.x} ${formatNumber(p.x_value ?? 0)}, ` +
              `${axes.y} ${formatNumber(p.y)}`}
          </title>
        </circle>
      ))}
    </>
  );
}

function Line({
  data,
  labels,
  x,
  y,
  baseline,
}: {
  data: ChartPoint[];
  labels: string[];
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
        <circle key={i} className="chart-dot" cx={x(i)} cy={y(p.y)} r={2.5}>
          <title>{`${labels[i]}: ${formatNumber(p.y)}`}</title>
        </circle>
      ))}
    </>
  );
}
