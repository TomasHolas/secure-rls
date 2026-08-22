/**
 * The Notes tab: the corpus the agent retrieves over, and a search box that runs the agent's own
 * retrieval path for a reader's query (ADR 0014). Two things become visible that the chat alone
 * only asserts — that a tenant's corpus is its own, and that "semantic search" is a real ranked
 * result with distances rather than a story the model tells about one.
 *
 * The rows the committed manifest plants an injection payload in are marked as such, which is
 * what makes the second-order injection demo concrete: point at the payload here, then ask the
 * agent something that retrieves it and watch it quote the text instead of obeying it.
 *
 * A missing note index is an operator condition, not a failure of the tab: the server answers
 * 503 with its own sentence and that sentence is what the reader is shown (ADR 0010 as amended).
 *
 * Every request here is guarded against its own staleness the same way - the corpus and the
 * manifest by the effect's `live` flag, the search by a ticket - so a slower earlier answer can
 * never overwrite a newer one and leave hits on screen for a query the reader has moved past.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../components/Button";
import { NoteList } from "../components/NoteList";
import { Pill } from "../components/Pill";
import { TextField } from "../components/forms";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import type { NoteEntry } from "../components/NoteList";
import { ApiError, browseNotes, listFlaggedNotes, searchNotes } from "../lib/api";
import type { BrowsePage, NoteHits } from "../lib/api";
import { formatCount, formatNumber } from "../lib/format";

const LOAD_FAILURE = "The notes could not be loaded.";
const SEARCH_FAILURE = "The search failed. Try again.";
const RETRIEVAL_NOTE =
  "This is rag.search_notes_scoped - the same partition-filtered vector search the agent's " +
  "search_notes tool calls, with the distance it scored each note by.";
const FIRST_PAGE = 1;
const USER_ID = "user_id";
const TENANT_ID = "tenant_id";
const NAME = "name";
const DEPARTMENT = "department";
const SCORE = "performance_score";
const NOTES = "notes";

export function NotesView({ tenant }: { tenant: string }) {
  const [page, setPage] = useState(FIRST_PAGE);
  const [corpus, setCorpus] = useState<BrowsePage | null>(null);
  const [flagged, setFlagged] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<NoteHits | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listFlaggedNotes()
      .then((planted) => {
        if (live) setFlagged(planted.kinds);
      })
      .catch(() => {
        // Nothing marked is the honest fallback: the manifest is a demo aid, not the data.
        if (live) setFlagged({});
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    browseNotes({ page })
      .then((serverPage) => {
        if (live) {
          setCorpus(serverPage);
          setError(null);
        }
      })
      .catch((cause) => {
        if (live) setError(cause instanceof ApiError ? cause.message : LOAD_FAILURE);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [page]);

  // Only the newest search may write: a slower earlier one must not overwrite it on arrival.
  const latest = useRef(0);

  const search = useCallback(() => {
    if (!query.trim()) return;
    const ticket = latest.current + 1;
    latest.current = ticket;
    setSearching(true);
    setSearchError(null);
    searchNotes(query)
      .then((found) => {
        if (ticket === latest.current) setHits(found);
      })
      .catch((cause) => {
        if (ticket !== latest.current) return;
        setHits(null);
        setSearchError(cause instanceof ApiError ? cause.message : SEARCH_FAILURE);
      })
      .finally(() => {
        if (ticket === latest.current) setSearching(false);
      });
  }, [query]);

  const pages = corpus ? Math.max(1, Math.ceil(corpus.total / corpus.page_size)) : 1;

  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="Notes"
        title="The note corpus"
        subtitle={`The free-text notes on the ${tenant} tenant's rows - the text the agent retrieves over, and the same rows it can never leave.`}
      />

      <Section
        title="Search the way the agent does"
        aside={hits ? <Pill tone="accent">top {formatNumber(hits.k)}</Pill> : null}
      >
        <form
          className="search-row"
          onSubmit={(event) => {
            event.preventDefault();
            search();
          }}
        >
          <TextField
            id="notes-query"
            label="Query"
            value={query}
            onChange={setQuery}
            placeholder="what the agent would be asked about"
          />
          <Button variant="primary" type="submit" disabled={searching || !query.trim()}>
            Search
          </Button>
        </form>
        {searchError ? <p className="form-error">{searchError}</p> : null}
        {hits ? (
          <>
            <p className="data-table-note">{RETRIEVAL_NOTE}</p>
            <NoteList
              notes={hits.hits}
              flagged={flagged}
              empty="No note of this tenant was close enough to that query."
            />
          </>
        ) : null}
      </Section>

      <Section
        title="Corpus"
        aside={
          corpus ? (
            <Pill tone="neutral">
              {formatCount(corpus.total, "note")} · page {formatNumber(corpus.page)} of{" "}
              {formatNumber(pages)}
            </Pill>
          ) : null
        }
      >
        {error ? <p className="form-error">{error}</p> : null}
        {corpus === null ? (
          <EmptyState icon="loader">{error ? "Nothing to show." : "Loading notes…"}</EmptyState>
        ) : (
          <>
            <NoteList
              notes={asNotes(corpus)}
              flagged={flagged}
              empty="This tenant has no notes."
            />
            <div className="pager">
              <Button
                onClick={() => setPage((current) => Math.max(FIRST_PAGE, current - 1))}
                disabled={loading || corpus.page <= FIRST_PAGE}
              >
                Previous
              </Button>
              <span className="pager-state">
                showing {formatNumber(corpus.rows.length)} of {formatCount(corpus.total, "note")}
              </span>
              <Button
                onClick={() => setPage((current) => current + 1)}
                disabled={loading || corpus.page >= pages}
              >
                Next
              </Button>
            </div>
          </>
        )}
      </Section>
    </Page>
  );
}

/**
 * One page of the corpus as note cards; the server names its columns, so read them by name.
 *
 * Every column `GET /notes` serves is carried onto the card — the card is the surface a reader
 * verifies a retrieval claim on, and a column fetched over the wire and dropped here would take
 * exactly what they verify against with it (issue #103).
 */
function asNotes(page: BrowsePage): NoteEntry[] {
  const id = page.columns.indexOf(USER_ID);
  const tenant = page.columns.indexOf(TENANT_ID);
  const name = page.columns.indexOf(NAME);
  const department = page.columns.indexOf(DEPARTMENT);
  const score = page.columns.indexOf(SCORE);
  const note = page.columns.indexOf(NOTES);
  return page.rows.map((row) => ({
    user_id: Number(row[id]),
    tenant_id: row[tenant] as string | undefined,
    name: String(row[name]),
    department: row[department] as string | undefined,
    performance_score: row[score] as number | undefined,
    note: String(row[note]),
  }));
}
