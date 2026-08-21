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
    { x_low: 40000, x_high: 60000, y: 18 },
    { x_low: 60000, x_high: 80000, y: 42 },
    { x_low: 80000, x_high: 100000, y: 35 },
    { x_low: 100000, x_high: 120000, y: 11 },
    { x_low: 120000, x_high: 140000, y: 4 },
  ],
};

const GROUPED: ChartSpec = {
  kind: "grouped_bar",
  title: "avg salary by department and score band",
  x_label: "department",
  y_label: "avg salary",
  series_label: "score band",
  data: [
    { x: "Engineering", series: "3", y: 118500 },
    { x: "Engineering", series: "4", y: 131200 },
    { x: "Sales", series: "3", y: 64100 },
    { x: "Sales", series: "4", y: 76400 },
    { x: "Sales", series: "5", y: 81000 },
  ],
};

const SCATTER: ChartSpec = {
  kind: "scatter",
  title: "performance score against salary",
  x_label: "salary",
  y_label: "performance score",
  data: [
    { x: "Ada", x_value: 155230, y: 4.2 },
    { x: "Alan", x_value: 98000, y: 3.4 },
    { x: "Amir", x_value: 61500, y: 2.9 },
  ],
};

const BOX: ChartSpec = {
  kind: "box",
  title: "salary spread by department",
  x_label: "department",
  y_label: "salary",
  data: [
    { x: "Engineering", y: 118500, q1: 101000, q3: 133000, low: 78000, high: 155230 },
    { x: "Sales", y: 64100, q1: 58000, q3: 72000, low: 45440, high: 89000 },
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

  it("renders a histogram bar per bin, labelled by its grouped lower edge", () => {
    const { container } = render(<Chart spec={HISTOGRAM} />);

    expect(container.querySelectorAll("rect.chart-bar")).toHaveLength(HISTOGRAM.data.length);
    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["40,000", "120,000"]));
    expect(ticks.some((tick) => tick?.includes("40000-60000"))).toBe(false);
  });

  it("names a bin's whole range, grouped, in its hover title", () => {
    const { container } = render(<Chart spec={HISTOGRAM} />);

    const titles = [...container.querySelectorAll("rect.chart-bar title")].map((t) => t.textContent);
    expect(titles[0]).toBe("40,000-60,000: 18");
  });

  it("groups thousands on the value axis, never a raw digit run", () => {
    const { container } = render(<Chart spec={BAR} />);

    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["0", "59,250", "118,500"]));
    const barTitle = container.querySelector("rect.chart-bar title")?.textContent;
    expect(barTitle).toBe("Engineering: 118,500");
  });

  it("renders a grouped bar per point, coloured and legended by series", () => {
    const { container } = render(<Chart spec={GROUPED} />);

    const bars = [...container.querySelectorAll("rect.chart-series")];
    expect(bars).toHaveLength(GROUPED.data.length);
    expect(bars[0].getAttribute("style")).toContain("var(--chart-1)");
    expect(bars[1].getAttribute("style")).toContain("var(--chart-2)");
    expect(bars[2].getAttribute("style")).toContain("var(--chart-1)");
    expect(bars[0].querySelector("title")?.textContent).toBe("Engineering / 3: 118,500");

    const legend = [...container.querySelectorAll(".chart-legend-item")].map((i) => i.textContent);
    expect(legend).toEqual(["3", "4", "5"]);
    expect(container.querySelector(".chart-legend-label")?.textContent).toBe("score band");
  });

  it("packs a grouped category's bars side by side without overlapping", () => {
    const { container } = render(<Chart spec={GROUPED} />);

    const bars = [...container.querySelectorAll("rect.chart-series")].map((r) => ({
      x: Number(r.getAttribute("x")),
      width: Number(r.getAttribute("width")),
    }));
    const [first, second] = bars;
    expect(first.width).toBeGreaterThan(0);
    expect(second.x).toBeGreaterThanOrEqual(first.x + first.width);
    expect(second.x).toBeCloseTo(first.x + first.width);
  });

  it("puts one x tick per category of a grouped bar, not one per bar", () => {
    const { container } = render(<Chart spec={GROUPED} />);

    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks.filter((tick) => tick === "Engineering")).toHaveLength(1);
    expect(ticks.filter((tick) => tick === "Sales")).toHaveLength(1);
  });

  it("renders a scatter as one dot per row on a numeric x axis", () => {
    const { container } = render(<Chart spec={SCATTER} />);

    const dots = [...container.querySelectorAll("circle.chart-point")];
    expect(dots).toHaveLength(SCATTER.data.length);
    expect(dots[0].querySelector("title")?.textContent).toBe(
      "Ada: salary 155,230, performance score 4.2",
    );
    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["61,500", "108,365", "155,230"]));
    expect(container.querySelectorAll("rect.chart-bar")).toHaveLength(0);
  });

  it("places the widest scatter dot at the right edge of the plot", () => {
    const { container } = render(<Chart spec={SCATTER} />);

    const centres = [...container.querySelectorAll("circle.chart-point")].map((c) =>
      Number(c.getAttribute("cx")),
    );
    expect(Math.max(...centres)).toBe(centres[0]);
    expect(Math.min(...centres)).toBe(centres[2]);
  });

  it("renders a box per group with its quartile box, median and whiskers", () => {
    const { container } = render(<Chart spec={BOX} />);

    expect(container.querySelectorAll("rect.chart-box")).toHaveLength(BOX.data.length);
    expect(container.querySelectorAll("line.chart-median")).toHaveLength(BOX.data.length);
    expect(container.querySelectorAll("line.chart-whisker")).toHaveLength(BOX.data.length * 3);
    expect(container.querySelector("rect.chart-box title")?.textContent).toBe(
      "Engineering: median 118,500, quartiles 101,000-133,000, whiskers 78,000-155,230",
    );
  });

  it("scales a box against its own whiskers, not against zero", () => {
    const { container } = render(<Chart spec={BOX} />);

    const ticks = [...container.querySelectorAll("text.chart-axis")].map((t) => t.textContent);
    expect(ticks).toEqual(expect.arrayContaining(["45,440", "155,230"]));
    expect(ticks).not.toContain("0");
  });

  it("keeps the title and shows the empty state when the spec carries no points", () => {
    const { container } = render(<Chart spec={{ ...BAR, data: [] }} />);

    expect(container.querySelector(".chart-title")?.textContent).toBe(BAR.title);
    expect(container.querySelector(".empty")?.textContent).toContain("No data to plot.");
    expect(container.querySelector("svg")).toBeNull();
  });

  it("reserves its height whether it plots or reports no data", () => {
    const plotted = render(<Chart spec={BAR} height={300} />);
    expect((plotted.container.querySelector(".chart") as HTMLElement).style.minHeight).toBe("300px");
    cleanup();

    const empty = render(<Chart spec={{ ...BAR, data: [] }} height={300} />);
    expect((empty.container.querySelector(".chart") as HTMLElement).style.minHeight).toBe("300px");
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
