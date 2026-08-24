/**
 * The Notes tab: the whole note corpus the agent retrieves over, and a search box that runs the
 * agent's own retrieval path for a reader's query (ADR 0014 as rewritten by issue #117).
 *
 * The asymmetry between those two is the demonstration, and it is deliberate. The LIST is the
 * dataset's - every tenant's notes, with a tenant filter so a reader can go straight to another
 * tenant's rows. The SEARCH is not: it is `rag.search_notes_scoped` for the signed-in tenant, the
 * same partition-filtered vector search the `search_notes` tool calls. So a reader can read
 * beta's planted injection payload in the list, search for its exact text as acme, and get
 * nothing back. Neither half proves much alone; together they are the point of the tab.
 *
 * The rows the committed manifest plants a payload in are marked as such, in every tenant now
 * that the list shows every tenant, which is what makes the second-order injection demo concrete:
 * point at the payload here, then ask the agent something that retrieves it and watch it quote
 * the text instead of obeying it.
 *
 * A missing note index is an operator condition, not a failure of the tab: the server answers
 * 503 with its own sentence and that sentence is what the reader is shown (ADR 0010 as amended).
 *
 * The corpus listing takes the same filters `/records` does, so it owes a reader the same
 * honesty about a parameter it does not read: the `ParamProbe` here appends one of the reader's
 * own to the corpus request and shows what the server reports it ignored (#107).
 *
 * Every request here is guarded against its own staleness the same way - the corpus and the
 * manifest by the effect's `live` flag, the search by a ticket - so a slower earlier answer can
 * never overwrite a newer one and leave hits on screen for a query the reader has moved past.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../components/Button";
import { Loader } from "../components/Loader";
import { NoteList } from "../components/NoteList";
import { ParamProbe } from "../components/ParamProbe";
import { Pill } from "../components/Pill";
import { SelectField, TextField } from "../components/forms";
import { EmptyState, Page, PageHeader, Section } from "../components/layout";
import type { NoteEntry } from "../components/NoteList";
import { ApiError, browseNotes, listFlaggedNotes, listTenants, searchNotes } from "../lib/api";
import type { BrowsePage, FilterOption, NoteHits } from "../lib/api";
import { formatCount, formatNumber } from "../lib/format";

const LOAD_FAILURE = "The notes could not be loaded.";
const SEARCH_FAILURE = "The search failed. Try again.";
const RETRIEVAL_NOTE =
  "This is rag.search_notes_scoped - the same partition-filtered vector search the agent's " +
  "search_notes tool calls, with the distance it scored each note by. It answers for your " +
  "tenant only, while the corpus below is the whole dataset: a note you can read there and not " +
  "retrieve here is the isolation, shown twice on one screen.";
const PROBE_TITLE = "Probe the request";
const ALL_TENANTS = "all tenants";
const FIRST_PAGE = 1;
const USER_ID = "user_id";
const TENANT_ID = "tenant_id";
const NAME = "name";
const DEPARTMENT = "department";
const SCORE = "performance_score";
const NOTES = "notes";

export function NotesView({ tenant }: { tenant: string }) {
  const [page, setPage] = useState(FIRST_PAGE);
  const [probe, setProbe] = useState("");
  const [filtered, setFiltered] = useState("");
  const [corpus, setCorpus] = useState<BrowsePage | null>(null);
  const [tenants, setTenants] = useState<FilterOption[]>([]);
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
    listTenants()
      .then((list) => {
        if (live) setTenants(list);
      })
      .catch(() => {
        if (live) setTenants([]);
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    browseNotes({ page, tenant_id: filtered }, probe)
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
  }, [page, filtered, probe]);

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

  // A select is one deliberate action, so it applies on change; a page of typing would not.
  const filterBy = useCallback((value: string) => {
    setPage(FIRST_PAGE);
    setFiltered(value);
  }, []);

  const pages = corpus ? Math.max(1, Math.ceil(corpus.total / corpus.page_size)) : 1;
  const scope = filtered ? `tenant ${filtered}` : ALL_TENANTS;

  return (
    <Page className="section-stack">
      <PageHeader
        eyebrow="Notes"
        title="The note corpus"
        subtitle={`Every tenant's free-text notes - the text the agent retrieves over. The search below answers for ${tenant} alone, which is what the list beside it makes checkable.`}
      />

      <Section
        title="Search the way the agent does"
        aside={hits ? <Pill tone="accent">top {formatNumber(hits.k)}</Pill> : null}
      >
        <form
          className="search-row control-row"
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
              empty={`No note of the ${tenant} tenant was close enough to that query.`}
            />
          </>
        ) : null}
      </Section>

      <Section title={PROBE_TITLE}>
        <ParamProbe
          id="notes-probe"
          ignored={corpus?.ignored ?? []}
          onSend={setProbe}
          disabled={loading}
        />
      </Section>

      <Section
        title="Corpus"
        aside={
          corpus ? (
            <Pill tone="neutral">
              {formatCount(corpus.total, "note")} · {scope} · page {formatNumber(corpus.page)} of{" "}
              {formatNumber(pages)}
            </Pill>
          ) : null
        }
      >
        <div className="search-row control-row">
          <SelectField
            id="notes-tenant"
            label="Tenant"
            value={filtered}
            options={tenants.map((entry) => ({
              value: entry.value,
              label: `${entry.value} (${formatNumber(entry.employees)})`,
            }))}
            onChange={filterBy}
            placeholder="any tenant"
            disabled={loading}
          />
        </div>
        {error ? <p className="form-error">{error}</p> : null}
        {corpus === null ? (
          error ? (
            <EmptyState>Nothing to show.</EmptyState>
          ) : (
            <Loader scale="page" label="Loading notes…" />
          )
        ) : (
          <>
            <NoteList
              notes={asNotes(corpus)}
              flagged={flagged}
              empty="No note in the dataset matches that filter."
            />
            <div className="pager">
              <Button
                onClick={() => setPage((current) => Math.max(FIRST_PAGE, current - 1))}
                disabled={loading || corpus.page <= FIRST_PAGE}
              >
                Previous
              </Button>
              <span className="pager-state">
                showing {formatNumber(corpus.rows.length)} of{" "}
                {formatCount(corpus.total, "note")} · {scope}
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
