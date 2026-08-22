/**
 * The one number formatter the UI has: every number a reader sees goes through it — chart
 * axis ticks, histogram bin edges, hover values, numeric DataTable cells, the elapsed seconds
 * a trace step reports and the counts its chips carry.
 *
 * It lives here and not in the backend because grouping digits is a presentation decision
 * tied to a locale, and the backend is locale-free by design: `analytics.py` emits raw
 * numbers (histogram bin edges included) and never a formatted string. The locale is pinned
 * rather than taken from the browser so the same data renders identically for every reader
 * of a shared trace, and so the tests assert one exact string.
 */

const LOCALE = "en-US";
/** Enough to keep a Tukey fence or a performance score exact, few enough to keep a tick short. */
const MAX_DECIMALS = 2;
const RANGE_SEPARATOR = "-";
const MS_PER_SECOND = 1000;
/** A duration reads as a speed, not a measurement: one decimal is all it carries. */
const SECOND_DECIMALS = 1;

const NUMBER = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: MAX_DECIMALS });

/** One number as a reader sees it: grouped thousands, no padding, at most two decimals. */
export function formatNumber(value: number): string {
  return NUMBER.format(value);
}

/** A closed numeric range, as a histogram bin's two edges. */
export function formatRange(low: number, high: number): string {
  return `${formatNumber(low)}${RANGE_SEPARATOR}${formatNumber(high)}`;
}

/** An elapsed span as a reader sees it: seconds to one decimal, because it reads as a duration. */
export function formatSeconds(milliseconds: number): string {
  return `${(milliseconds / MS_PER_SECOND).toFixed(SECOND_DECIMALS)}s`;
}

/** A count with its noun, singular when there is one of it. */
export function formatCount(count: number, noun: string, plural = `${noun}s`): string {
  return `${formatNumber(count)} ${count === 1 ? noun : plural}`;
}
