/**
 * GlideList - a row group with ONE highlight that glides to the row the pointer or the keyboard
 * is on, instead of every row lighting a background of its own (issue #114, pattern from
 * beautifului.dev). The travel is what says the rows are one group; a background per row says
 * nothing but "this one".
 *
 * The highlight is placed from the row's own offsets, so where that measurement has not happened
 * - no layout yet, or no scripting - the stylesheet's plain `:hover` on each row is what a reader
 * gets, and the `gliding` class is what turns it off in favour of the travelling one.
 *
 * `hidden` is for a container that clips the group out of view: it leaves the accessibility tree
 * and stops tracking. Rows are the caller's `<li>` children; the highlight is one more.
 */

import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

interface Row {
  top: number;
  height: number;
}

export function GlideList({ hidden, children }: { hidden?: boolean; children: ReactNode }) {
  const [row, setRow] = useState<Row | null>(null);
  const glide = hidden ? null : row;

  function follow(event: SyntheticEvent): void {
    const item = (event.target as HTMLElement).closest<HTMLElement>("li");
    setRow(item ? { top: item.offsetTop, height: item.offsetHeight } : null);
  }

  return (
    <ul
      className={glide ? "sidebar-list gliding" : "sidebar-list"}
      aria-hidden={hidden || undefined}
      onPointerMove={hidden ? undefined : follow}
      onPointerLeave={() => setRow(null)}
      onFocus={hidden ? undefined : follow}
      onBlur={() => setRow(null)}
    >
      <li
        className="rail-glide"
        aria-hidden="true"
        style={glide ? { transform: `translateY(${glide.top}px)`, height: glide.height } : undefined}
      />
      {children}
    </ul>
  );
}
