/**
 * NoteList — employee-written notes as quoted data, never as instructions. The chat trace shows
 * what the agent retrieved through it, and the Notes tab shows the corpus and its search hits
 * through the same brick, so a note reads identically wherever it is served (ADR 0014).
 *
 * `distance` is the retrieval score and is shown whenever there is one: a corpus listing has
 * none, a search hit does. `flagged` marks the rows the committed poison manifest plants an
 * injection payload in — the manifest is public, and pointing at a payload before the agent
 * reads it is the second-order injection story made concrete.
 */

import { Pill } from "./Pill";

/** One note as the backend serves it; `distance` is present only when retrieval produced it. */
export interface NoteEntry {
  user_id: number;
  name: string;
  note: string;
  distance?: number;
}

const DISTANCE_DECIMALS = 3;
const FLAGGED_LABEL = "planted payload";

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
        return (
          <li className="note-card" key={`${note.user_id}-${index}`}>
            <div className="note-head">
              <span className="note-name">{note.name}</span>
              {kind === undefined ? null : (
                <Pill tone="warn" icon="filter" title={`poisoned_manifest.json: ${kind}`}>
                  {FLAGGED_LABEL}
                </Pill>
              )}
              <span className="note-meta">
                #{note.user_id}
                {note.distance === undefined
                  ? null
                  : ` · distance ${note.distance.toFixed(DISTANCE_DECIMALS)}`}
              </span>
            </div>
            <p className="note-text">{note.note}</p>
          </li>
        );
      })}
    </ul>
  );
}
