/** The one number formatter: grouped thousands everywhere a reader meets a number. */

import { describe, expect, it } from "vitest";

import { formatNumber, formatRange } from "./format";

describe("formatNumber", () => {
  it("groups thousands", () => {
    expect(formatNumber(155230)).toBe("155,230");
    expect(formatNumber(1000)).toBe("1,000");
    expect(formatNumber(1234567)).toBe("1,234,567");
  });

  it("leaves a small number alone", () => {
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(42)).toBe("42");
  });

  it("keeps a fence or a score exact but never pads decimals", () => {
    expect(formatNumber(-113.75)).toBe("-113.75");
    expect(formatNumber(3.5)).toBe("3.5");
    expect(formatNumber(118500.0)).toBe("118,500");
  });

  it("rounds off digits no reader needs", () => {
    expect(formatNumber(3.46666)).toBe("3.47");
  });
});

describe("formatRange", () => {
  it("labels a histogram bin from its two numeric edges", () => {
    expect(formatRange(155230, 174165)).toBe("155,230-174,165");
    expect(formatRange(2, 3)).toBe("2-3");
  });
});
