/**
 * The debounce brick: the one place that knows how long a filter waits for the reader to stop
 * typing before it applies itself (issue #152).
 *
 * A filter that needs a button to take effect is a filter that lies - a pressed tenant chip
 * beside a table still showing every tenant was the defect this exists for. So every control
 * applies itself, and the only question left is when: a chip or a select is one deliberate act
 * and applies on the change, while a text box would otherwise fire a request per character. This
 * hook is that difference, held once rather than re-timed in each view.
 *
 * It hands back three things because a caller needs all three. `schedule` applies the last value
 * of a burst once the burst has stopped. `cancel` drops a scheduled value that a later action has
 * already subsumed - a Reset, or a chip click that applies the whole draft on the spot - so a
 * stale keystroke can never land after it. `pending` is the interval itself, made visible: while
 * it is true the reader is mid-thought, and a view can hold back a server refusal that a
 * half-typed value earned rather than painting it over the table between two keystrokes.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** How long a typed filter waits for the next keystroke before it applies itself. */
export const FILTER_DEBOUNCE_MS = 350;

/** A scheduler over one value, its cancel, and whether a value is waiting to be applied. */
export interface Debounced<T> {
  schedule: (value: T) => void;
  cancel: () => void;
  pending: boolean;
}

/**
 * Applies only the last value of a burst, once the burst has been quiet for `delay` milliseconds.
 *
 * `apply` may be a fresh closure on every render - the newest one is what a due value is applied
 * through, so a caller never has to memoize it to keep the timer from restarting.
 */
export function useDebounced<T>(
  apply: (value: T) => void,
  delay: number = FILTER_DEBOUNCE_MS,
): Debounced<T> {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const newest = useRef(apply);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    newest.current = apply;
  }, [apply]);

  const cancel = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
    setPending(false);
  }, []);

  // A value scheduled by a view that has gone away must not be applied into it.
  useEffect(() => cancel, [cancel]);

  const schedule = useCallback(
    (value: T) => {
      if (timer.current !== null) clearTimeout(timer.current);
      setPending(true);
      timer.current = setTimeout(() => {
        timer.current = null;
        setPending(false);
        newest.current(value);
      }, delay);
    },
    [delay],
  );

  return { schedule, cancel, pending };
}
