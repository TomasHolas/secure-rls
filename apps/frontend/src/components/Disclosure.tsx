/**
 * Disclosure — the trace's fold, off the timeline: a quiet chevron toggle whose body is in the
 * document only while it is open, and which is closed until a reader asks (issue #139).
 *
 * The interaction is `chat/TraceStep`'s and deliberately not a second idiom — a plain button
 * carrying `aria-expanded`, the chevron turning from right to down — because that step is a
 * timeline row with an icon rail and cannot be dropped under a table. A native `<details>` was
 * the other option and brings its own marker, its own focus behaviour and a body that stays in
 * the accessibility tree while collapsed.
 *
 * `show` and `hide` are both the caller's copy: a trigger still reading "show" once it has shown
 * misdescribes what the next click does, and the two labels are what a screen reader hears beside
 * the expanded state.
 */

import { useState } from "react";
import type { ReactNode } from "react";

import { Icon } from "./Icon";

const CHEVRON = 14;

export function Disclosure({
  show,
  hide,
  children,
}: {
  show: string;
  hide: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="disclosure">
      <button
        type="button"
        className="disclosure-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name={open ? "chevron-down" : "chevron-right"} size={CHEVRON} />
        {open ? hide : show}
      </button>
      {open ? <div className="disclosure-body">{children}</div> : null}
    </div>
  );
}
