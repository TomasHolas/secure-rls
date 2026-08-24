/**
 * Loader — the one thing on screen that says work is in flight, everywhere the app used to spin a
 * glyph (issue #123): a 3x3 pixel grid whose chevron wavefront sweeps across it, an optional
 * shimmering label, and an optional live elapsed time. Ported from the loader on
 * beautifului.dev onto our own CSS and tokens (ADR 0006) - no Tailwind, no dependency, and the
 * grid is DOM rather than a glyph, because `Icon` is a fixed Material Symbols subset.
 *
 * It composes rather than insisting: the grid alone is the whole loader on a pending button or in
 * the trace's header, where the text beside it already says what is happening; a label turns it
 * into the loading state of a panel; a `since` timestamp adds the elapsed time where how long
 * this is taking is the reader's actual question - the model thinking.
 *
 * Every metric of the grid (cell, gap, cycle, the two opacities) is a custom property in
 * `app.css`, so nothing here carries a number that a designer would then have to find in JSX.
 *
 * Accessibility: the wrapper is the status region and the grid is hidden from it, so what is
 * announced is the label - real text, not a picture of text. With no label it announces nothing,
 * which is right where the adjacent text already carries the message. The elapsed time is hidden
 * too: it changes ten times a second, and a live region that noisy is unusable rather than
 * informative. What a screen reader hears is the settled summary the caller renders afterwards.
 */

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { formatSeconds } from "../lib/format";

/** The 3x3: `app.css` assigns each of these nine cells its ring in the chevron. */
const CELLS = 9;
/** `formatSeconds` shows a tenth of a second, so the clock is read at that resolution, no finer. */
const TICK_MS = 100;

/** `inline` sits in a row of text or in a button; `page` stands alone where a panel has no content yet. */
export type LoaderScale = "inline" | "page";

export function Loader({
  label,
  since,
  scale = "inline",
}: {
  label?: ReactNode;
  since?: number | null;
  scale?: LoaderScale;
}) {
  return (
    <span className={`loader loader-${scale}`} role="status">
      <span className="loader-grid" aria-hidden="true">
        {Array.from({ length: CELLS }, (_, cell) => (
          <span className="loader-cell" key={cell} />
        ))}
      </span>
      {label ? <span className="loader-label">{label}</span> : null}
      {typeof since === "number" ? <Elapsed since={since} /> : null}
    </span>
  );
}

/**
 * How long this has been running, taken from the clock on every tick rather than accumulated by
 * one: a counter that adds its own interval under-reports a long turn by however much the timer
 * was late, and a turn may run to the deadline. Formatted by `lib/format.ts` like every other
 * number a reader sees.
 */
function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <span className="loader-elapsed" aria-hidden="true">
      {formatSeconds(now - since)}
    </span>
  );
}
