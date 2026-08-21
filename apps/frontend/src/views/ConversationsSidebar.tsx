/**
 * The conversation history rail: the signed-in identity's threads, newest first, with the
 * open one highlighted. It renders what `lib/conversations.ts` holds and calls back into
 * it - no fetching of its own, so the sidebar and the chat view can never disagree about
 * which thread is open.
 *
 * The list is whatever `GET /conversations` returned, which is scoped to the caller's
 * `sub` and `tenant_id` server-side (ADR 0012): a re-login as another identity lists that
 * identity's threads because the API says so, not because this view filters anything.
 *
 * New chat opens an empty draft rather than posting a thread straight away - the title is
 * the first question (ADR 0012) and the registry has no title update, so a thread created
 * before there is a question could only ever carry the placeholder title.
 */

import { useState } from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { Sidebar } from "../components/layout";
import type { ConversationsStore } from "../lib/conversations";
import type { Thread } from "../lib/api";

export function ConversationsSidebar({ store }: { store: ConversationsStore }) {
  const [pendingDelete, setPendingDelete] = useState<Thread | null>(null);

  function confirmDelete(): void {
    if (pendingDelete) store.remove(pendingDelete.thread_id);
    setPendingDelete(null);
  }

  return (
    <>
      <Sidebar
        title="Conversations"
        actions={
          <Button variant="ghost" className="side-add" onClick={store.newChat}>
            <Icon name="plus" size={14} /> New chat
          </Button>
        }
      >
        {store.error ? <p className="form-error">{store.error}</p> : null}
        {store.threads.length === 0 ? (
          <p className="sidebar-note">
            {store.loading ? "Loading your conversations." : "No conversations yet."}
          </p>
        ) : (
          <ul className="sidebar-list">
            {store.threads.map((thread) => {
              const active = thread.thread_id === store.activeId;
              return (
                <li key={thread.thread_id} className={active ? "rail-item active" : "rail-item"}>
                  <button
                    type="button"
                    className="rail-item-open"
                    onClick={() => store.select(thread.thread_id)}
                    aria-current={active ? "true" : undefined}
                  >
                    <span className="rail-item-title">{thread.title}</span>
                    <span className="rail-item-meta">{formatCreated(thread.created)}</span>
                  </button>
                  <button
                    type="button"
                    className="btn-icon rail-item-delete"
                    onClick={() => setPendingDelete(thread)}
                    aria-label={`Delete conversation ${thread.title}`}
                    title="Delete this conversation"
                  >
                    <Icon name="trash" size={15} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
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
