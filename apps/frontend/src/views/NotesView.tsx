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
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "../components/Button";
import { NoteList } from "../components/NoteList";
import { Pill } from "../components/Pill";
import { TextField } from "../components/forms";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import type { NoteEntry } from "../components/NoteList";
import { ApiError, browseNotes, listFlaggedNotes, searchNotes } from "../lib/api";
import type { BrowsePage, NoteHits } from "../lib/api";
import { formatNumber } from "../lib/format";

const LOAD_FAILURE = "The notes could not be loaded.";
const SEARCH_FAILURE = "The search failed. Try again.";
const RETRIEVAL_NOTE =
  "This is rag.search_notes_scoped - the same partition-filtered vector search the agent's " +
  "search_notes tool calls, with the distance it scored each note by.";
const FIRST_PAGE = 1;
const USER_ID = "user_id";
const NAME = "name";
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

  const search = useCallback(() => {
    if (!query.trim()) return;
    setSearching(true);
    setSearchError(null);
    searchNotes(query)
      .then(setHits)
      .catch((cause) => {
        setHits(null);
        setSearchError(cause instanceof ApiError ? cause.message : SEARCH_FAILURE);
      })
      .finally(() => setSearching(false));
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
              {formatNumber(corpus.total)} notes · page {formatNumber(corpus.page)} of{" "}
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
                showing {formatNumber(corpus.rows.length)} of {formatNumber(corpus.total)} notes
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

/** One page of the corpus as note cards; the server names its columns, so read them by name. */
function asNotes(page: BrowsePage): NoteEntry[] {
  const id = page.columns.indexOf(USER_ID);
  const name = page.columns.indexOf(NAME);
  const note = page.columns.indexOf(NOTES);
  return page.rows.map((row) => ({
    user_id: Number(row[id]),
    name: String(row[name]),
    note: String(row[note]),
  }));
}
