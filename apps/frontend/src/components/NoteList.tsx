/**
 * NoteList — employee-written notes as quoted data, never as instructions. The chat trace shows
 * what the agent retrieved through it, and the Notes tab shows the corpus and its search hits
 * through the same brick, so a note reads identically wherever it is served (ADR 0014).
 *
 * A card shows what a reader checks the note against, not the text alone: the employee, their
 * department, their performance score — the notes are composed coherent with that score (ADR
 * 0008), so tone against number is a check anyone can make at a glance — and the tenant the row
 * came from, which is the isolation claim in the data itself. Salary and hire date are
 * deliberately not here: neither says anything about whether a text hit is the right one.
 *
 * Every field but the employee and the note is optional, because a caller may not have it: the
 * chat trace hands over what the retrieval returned, the Notes tab what the browse path served.
 * `distance` is the retrieval score and is shown whenever there is one: a corpus listing has
 * none, a search hit does. `flagged` marks the rows the committed poison manifest plants an
 * injection payload in — the manifest is public, and pointing at a payload before the agent
 * reads it is the second-order injection story made concrete.
 */

import { formatNumber } from "../lib/format";
import { Pill } from "./Pill";

/** One note as the backend serves it, keyed as the payload keys it; only two fields are certain. */
export interface NoteEntry {
  user_id: number;
  name: string;
  note: string;
  tenant_id?: string;
  department?: string;
  performance_score?: number;
  distance?: number;
}

const DISTANCE_DECIMALS = 3;
const FLAGGED_LABEL = "planted payload";
const SEPARATOR = " · ";
const SCORE_LABEL = "score";
const DISTANCE_LABEL = "distance";

export function NoteList({
  notes,
  flagged,
  empty = "No notes matched.",
}: {
  notes: NoteEntry[];
  flagged?: Record<string, string>;
  empty?: string;
}) {
  if (notes.length === 0) return <p className="data-table-note">{empty}</p>;

  return (
    <ul className="note-list">
      {notes.map((note, index) => {
        const kind = flagged?.[String(note.user_id)];
        const detail = facts(note);
        return (
          <li className="note-card" key={`${note.user_id}-${index}`}>
            <div className="note-head">
              <span className="note-name">{note.name}</span>
              {detail === "" ? null : <span className="note-facts">{detail}</span>}
              {kind === undefined ? null : (
                <Pill tone="warn" icon="filter" title={`poisoned_manifest.json: ${kind}`}>
                  {FLAGGED_LABEL}
                </Pill>
              )}
              <span className="note-meta">{provenance(note)}</span>
            </div>
            <p className="note-text">{note.note}</p>
          </li>
        );
      })}
    </ul>
  );
}

/** What the note is checked against: the employee's department and the score its tone matches. */
function facts(note: NoteEntry): string {
  return [
    note.department,
    note.performance_score === undefined
      ? null
      : `${SCORE_LABEL} ${formatNumber(note.performance_score)}`,
  ]
    .filter((fact) => fact)
    .join(SEPARATOR);
}

/** Where the note came from: its row, its tenant, and the distance retrieval scored it by. */
function provenance(note: NoteEntry): string {
  return [
    `#${note.user_id}`,
    note.tenant_id,
    note.distance === undefined
      ? null
      : `${DISTANCE_LABEL} ${note.distance.toFixed(DISTANCE_DECIMALS)}`,
  ]
    .filter((part) => part)
    .join(SEPARATOR);
}
