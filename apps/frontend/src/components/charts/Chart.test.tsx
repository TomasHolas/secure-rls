/** Chart brick fixtures: one ChartSpec per kind, asserted on the SVG the brick produces. */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Chart, type ChartSpec } from "./Chart";

afterEach(cleanup);

const BAR: ChartSpec = {
  kind: "bar",
  title: "avg salary by department",
  x_label: "department",
  y_label: "avg salary",
  data: [
    { x: "Engineering", y: 118500 },
    { x: "Finance", y: 94200 },
    { x: "Marketing", y: 81000 },
    { x: "Sales", y: 76400 },
  ],
};

const LINE: ChartSpec = {
  kind: "line",
  title: "employees by hire year",
  x_label: "hire year",
  y_label: "employees",
  data: [
    { x: "2019", y: 12 },
    { x: "2020", y: 31 },
    { x: "2021", y: 27 },
    { x: "2022", y: 44 },
    { x: "2023", y: 38 },
  ],
};

const HISTOGRAM: ChartSpec = {
  kind: "histogram",
  title: "salary distribution",
  x_label: "salary",
  y_label: "employees",
  data: [
    { x: "40000-60000", y: 18 },
    { x: "60000-80000", y: 42 },
    { x: "80000-100000", y: 35 },
    { x: "100000-120000", y: 11 },
    { x: "120000-140000", y: 4 },
  ],
};

describe("Chart", () => {
  it("renders a bar chart with one bar per point and both axis titles", () => {
    const { container } = render(<Chart spec={BAR} />);

    expect(container.querySelectorAll("rect.chart-bar")).toHaveLength(BAR.data.length);
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(BAR.title);
    const axisTitles = [...container.querySelectorAll("text.chart-axis-title")].map((t) => t.textContent);
    expect(axisTitles).toEqual([BAR.x_label, BAR.y_label]);
    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["Engineering", "Sales"]));
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("renders a line chart as one polyline plus a dot per point", () => {
    const { container } = render(<Chart spec={LINE} />);

    const polyline = container.querySelector("polyline.chart-line");
    expect(polyline?.getAttribute("points")?.trim().split(/\s+/)).toHaveLength(LINE.data.length);
    expect(container.querySelectorAll("circle.chart-dot")).toHaveLength(LINE.data.length);
    expect(container.querySelector("polygon.chart-area")).not.toBeNull();
    expect(container.querySelectorAll("rect.chart-bar")).toHaveLength(0);
  });

  it("renders a histogram bar per bin, labelled by the bin edges", () => {
    const { container } = render(<Chart spec={HISTOGRAM} />);

    expect(container.querySelectorAll("rect.chart-bar")).toHaveLength(HISTOGRAM.data.length);
    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["40000-60000", "120000-140000"]));
  });

  it("keeps the title and shows the empty state when the spec carries no points", () => {
    const { container } = render(<Chart spec={{ ...BAR, data: [] }} />);

    expect(container.querySelector(".chart-title")?.textContent).toBe(BAR.title);
    expect(container.querySelector(".empty")?.textContent).toContain("No data to plot.");
    expect(container.querySelector("svg")).toBeNull();
  });

  it("scales bars against the largest value, so the tallest bar spans the plot", () => {
    const { container } = render(<Chart spec={BAR} />);

    const heights = [...container.querySelectorAll("rect.chart-bar")].map((r) =>
      Number(r.getAttribute("height")),
    );
    expect(Math.max(...heights)).toBe(heights[0]);
    expect(heights).toEqual([...heights].sort((a, b) => b - a));
  });
});
