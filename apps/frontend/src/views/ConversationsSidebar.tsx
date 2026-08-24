/**
 * The conversation history rail: the signed-in identity's threads, newest first, with the
 * open one highlighted. It renders what `lib/conversations.ts` holds and calls back into
 * it - no fetching of its own, so the sidebar and the chat view can never disagree about
 * which thread is open.
 *
 * The list is whatever `GET /conversations` returned, which is scoped to the caller's
 * `sub` and `tenant_id` server-side (ADR 0012): a re-login as another identity lists that
 * identity's threads because the API says so, not because this view filters anything. The
 * inline search does filter, and only ever over the titles already on screen - it is a way to
 * find a thread in a long rail, never a query the server answers.
 *
 * New chat opens an empty draft rather than posting a thread straight away: a thread created
 * before there is a question would sit in this rail under the placeholder title.
 *
 * A title is text, rendered as text. It starts as the first question and is replaced by the
 * label the model generated for the thread (ADR 0012 as amended), which makes it model output:
 * it goes into the DOM as a text node here and in the delete dialog, never through Markdown
 * and never as markup, and the server has already stripped it down to one displayable line.
 * It is also the row's `title`, so a truncated thread reads in full on hover.
 *
 * A title is also the reader's to change: the pencil on a row turns that text node into an input
 * and `store.rename` sends what they typed. Which name wins afterwards is the server's rule, not
 * this view's - the PATCH stamps the row as renamed and no generated label writes over it again
 * (ADR 0012 as amended), so a rename landing during a titling window needs nothing here.
 *
 * The rail's shape is the beautifului.dev sidebar's, reimplemented on our tokens (issue #114,
 * `docs/ui-pattern-review.md`): a collapse that clips instead of re-laying out, one gliding hover
 * highlight, and the search growing out of its own icon.
 */

import { useRef, useState } from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "../components/Button";
import { GlideList } from "../components/GlideList";
import { Icon } from "../components/Icon";
import { InlineSearch } from "../components/InlineSearch";
import { Sidebar, useSidebarCollapsed } from "../components/layout";
import type { ConversationsStore } from "../lib/conversations";
import type { Thread } from "../lib/api";

export function ConversationsSidebar({ store }: { store: ConversationsStore }) {
  const [pendingDelete, setPendingDelete] = useState<Thread | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  function confirmDelete(): void {
    if (pendingDelete) store.remove(pendingDelete.thread_id);
    setPendingDelete(null);
  }

  return (
    <>
      <Sidebar
        title="Conversations"
        search={<RailSearch value={query} onChange={setQuery} />}
        actions={
          <Button variant="ghost" className="side-add" onClick={store.newChat}>
            <Icon name="plus" size={14} />
            <span className="rail-copy">New chat</span>
          </Button>
        }
      >
        {store.error ? <p className="form-error">{store.error}</p> : null}
        <RailThreads
          store={store}
          query={query}
          renaming={renaming}
          onRename={setRenaming}
          onDelete={setPendingDelete}
        />
      </Sidebar>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete conversation?"
        message={
          <>
            This permanently removes <strong>{pendingDelete?.title}</strong> and the memory the
            agent keeps for it. It cannot be undone.
          </>
        }
        onCancel={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
      />
    </>
  );
}

/** The head row's search, told by the rail itself when it has been clipped out of view. */
function RailSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const collapsed = useSidebarCollapsed();
  return (
    <InlineSearch
      id="rail-search"
      label="Search conversations"
      placeholder="Search chats"
      value={value}
      onChange={onChange}
      hidden={collapsed}
    />
  );
}

/**
 * The thread rows: the ones whose title matches the query, in the order the API returned them.
 * Collapsed, they are clipped rather than unmounted - that is what keeps the icons above them
 * from moving - so every row here leaves the Tab order and the accessibility tree instead.
 */
function RailThreads({
  store,
  query,
  renaming,
  onRename,
  onDelete,
}: {
  store: ConversationsStore;
  query: string;
  renaming: string | null;
  onRename: (threadId: string | null) => void;
  onDelete: (thread: Thread) => void;
}) {
  const collapsed = useSidebarCollapsed();
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? store.threads.filter((thread) => thread.title.toLowerCase().includes(needle))
    : store.threads;

  if (store.threads.length === 0) {
    return (
      <p className="sidebar-note" aria-hidden={collapsed || undefined}>
        {store.loading ? "Loading your conversations." : "No conversations yet."}
      </p>
    );
  }

  if (shown.length === 0) {
    return (
      <p className="sidebar-note" aria-hidden={collapsed || undefined}>
        No chats found.
      </p>
    );
  }

  return (
    <GlideList hidden={collapsed}>
      {shown.map((thread) => {
        const active = thread.thread_id === store.activeId;
        if (thread.thread_id === renaming) {
          return (
            <li key={thread.thread_id} className={active ? "rail-item active" : "rail-item"}>
              <RailRename
                thread={thread}
                onSave={(title) => store.rename(thread.thread_id, title)}
                onClose={() => onRename(null)}
              />
            </li>
          );
        }
        return (
          <li key={thread.thread_id} className={active ? "rail-item active" : "rail-item"}>
            <button
              type="button"
              className="rail-item-open"
              onClick={() => store.select(thread.thread_id)}
              aria-current={active ? "true" : undefined}
              title={thread.title}
              tabIndex={collapsed ? -1 : undefined}
            >
              <span className="rail-item-title">{thread.title}</span>
              <span className="rail-item-meta">{formatCreated(thread.created)}</span>
            </button>
            <button
              type="button"
              className="btn-icon rail-item-action"
              onClick={() => onRename(thread.thread_id)}
              aria-label={`Rename conversation ${thread.title}`}
              title="Rename this conversation"
              tabIndex={collapsed ? -1 : undefined}
            >
              <Icon name="edit" size={15} />
            </button>
            <button
              type="button"
              className="btn-icon rail-item-action rail-item-delete"
              onClick={() => onDelete(thread)}
              aria-label={`Delete conversation ${thread.title}`}
              title="Delete this conversation"
              tabIndex={collapsed ? -1 : undefined}
            >
              <Icon name="trash" size={15} />
            </button>
          </li>
        );
      })}
    </GlideList>
  );
}

/**
 * A row's title while the reader is renaming it: the same box, now an input over the same
 * timestamp, so the row keeps its height and its width whichever state it is in.
 *
 * Enter and blur save, Escape cancels, and a box left empty cancels too - a blank title is a 400
 * server-side, so it is never sent. Saving on blur is what an inline rename does everywhere: the
 * reader's undo is the key that says cancel, not clicking away. Whichever of the three settles
 * the edit first is the one that counts - the flag is what stops the blur that follows an Enter
 * or an Escape from saving a second time.
 */
function RailRename({
  thread,
  onSave,
  onClose,
}: {
  thread: Thread;
  onSave: (title: string) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(thread.title);
  const settled = useRef(false);

  function finish(save: boolean): void {
    if (settled.current) return;
    settled.current = true;
    const named = draft.trim();
    if (save && named && named !== thread.title) onSave(named);
    onClose();
  }

  return (
    // The row's group handlers - the glide's pointer tracking, the click that opens a thread -
    // have no business in a text box the reader is typing a name into.
    <span
      className="rail-item-edit"
      onPointerMove={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      <input
        className="rail-rename-input"
        aria-label="Rename conversation"
        value={draft}
        autoFocus
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== "Escape") return;
          event.stopPropagation();
          finish(event.key === "Enter");
        }}
        onBlur={() => finish(true)}
      />
      <span className="rail-item-meta">{formatCreated(thread.created)}</span>
    </span>
  );
}

/** The registry's ISO timestamp as a short local date and time; unparseable text stays as it is. */
function formatCreated(created: string): string {
  const at = new Date(created);
  if (Number.isNaN(at.getTime())) return created;
  return at.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
