/**
 * TraceStep — one entry in the live trace: an icon and title on a left rail, optional
 * status chips on the right, and whatever the step has to show underneath. Ported from
 * KB's `.md-item` timestamped log row (its date gutter becomes the icon rail) with the
 * dot from `.keypoints li .bullet`; KB has no timeline component to take whole.
 *
 * The tone carries the state in a second channel next to the icon, per KB's rule that
 * color is never the only signal: `blocked` is the red refusal state, `warn` the amber
 * retry, `muted` a plain graph step.
 */

import type { ReactNode } from "react";

import { Icon } from "../Icon";

export type StepTone = "default" | "muted" | "warn" | "blocked";

export function TraceStep({
  icon,
  title,
  meta,
  tone = "default",
  children,
}: {
  icon: string;
  title: ReactNode;
  meta?: ReactNode;
  tone?: StepTone;
  children?: ReactNode;
}) {
  return (
    <li className={`trace-step trace-step-${tone}`}>
      <div className="trace-step-head">
        <span className="trace-step-icon">
          <Icon name={icon} size={15} />
        </span>
        <span className="trace-step-title">{title}</span>
        {meta ? <span className="trace-step-meta">{meta}</span> : null}
      </div>
      {children ? <div className="trace-step-body">{children}</div> : null}
    </li>
  );
}
