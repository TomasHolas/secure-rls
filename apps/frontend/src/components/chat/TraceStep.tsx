/**
 * TraceStep — one entry in the live trace: an icon and title on a left rail, optional
 * status chips on the right, and whatever the step has to show underneath. Ported from
 * KB's `.md-item` timestamped log row (its date gutter becomes the icon rail) with the
 * dot from `.keypoints li .bullet`; KB has no timeline component to take whole.
 *
 * The tone carries the state in a second channel next to the icon, per KB's rule that
 * color is never the only signal: `blocked` is the red refusal state, `warn` the amber
 * retry, `muted` a plain graph step.
 *
 * A step with something to show is a disclosure of its own (the same chevron the panel
 * head uses): its head is a button carrying `aria-expanded`, so a long step can be folded
 * away instead of every SQL statement, table and chart being open at once. `open` sets the
 * state it starts in - reasoning starts closed, an outcome open - and the reader's click
 * wins from then on. A step with nothing underneath stays a plain row with no control.
 */

import { useState } from "react";
import type { ReactNode } from "react";

import { Icon } from "../Icon";

export type StepTone = "default" | "muted" | "warn" | "blocked";

export function TraceStep({
  icon,
  title,
  meta,
  tone = "default",
  open = true,
  children,
}: {
  icon: string;
  title: ReactNode;
  meta?: ReactNode;
  tone?: StepTone;
  open?: boolean;
  children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(open);
  const head = (
    <>
      <span className="trace-step-icon">
        <Icon name={icon} size={15} />
      </span>
      {children ? (
        <Icon name={expanded ? "chevron-down" : "chevron-right"} size={14} />
      ) : null}
      <span className="trace-step-title">{title}</span>
      {meta ? <span className="trace-step-meta">{meta}</span> : null}
    </>
  );
  return (
    <li className={`trace-step trace-step-${tone}`}>
      {children ? (
        <button
          type="button"
          className="trace-step-head trace-step-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {head}
        </button>
      ) : (
        <div className="trace-step-head">{head}</div>
      )}
      {children && expanded ? <div className="trace-step-body">{children}</div> : null}
    </li>
  );
}
