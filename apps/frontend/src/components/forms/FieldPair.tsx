/**
 * FieldPair — the two bounds of one filter as a single grid cell, so a wrap can never put
 * `from` on one row and `to` on the next (issue #115): the filter grid lays out the pair, not
 * the two fields, and there is no width at which the grid can separate them.
 *
 * Presentational only. Each field keeps its own label and its own id, so what a screen reader
 * announces is unchanged - the pair is a layout fact, not a new control.
 */

import type { ReactNode } from "react";

export function FieldPair({ children }: { children: ReactNode }) {
  return <div className="field-pair">{children}</div>;
}
