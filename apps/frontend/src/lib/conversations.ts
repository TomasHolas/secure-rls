/**
 * The conversation-state brick: one owner for "which thread am I in, what threads do I
 * have, and what did they say". The sidebar and the chat view are siblings that both need
 * it, so it lives here rather than in either of them (CLAUDE.md, one concern one module).
 *
 * A thread is created lazily, when the first question titles it: a thread posted on the New
 * chat click would sit in the rail under the placeholder title until it was asked something.
 * New chat therefore opens an empty draft and `startThread` registers it the moment there is
 * a question.
 *
 * `titleThread` is the second half of that (ADR 0012 as amended): once the first turn is over,
 * the server generates the few-word label from the exchange and this store adopts the row it
 * answers with. Adopting the response rather than re-listing is deliberate - the PATCH body IS
 * the stored row, so one request settles it and the rail cannot reorder or flicker around the
 * thread the reader is looking at. A failed refresh is logged and nothing else: the thread
 * keeps the first-message title it already had, which is a title, not an error to report.
 *
 * `replay` holds what the server remembers of the open thread, already folded into turns by
 * `lib/trace.ts`: the questions, the answers, and the whole trace each turn produced - its
 * reasoning, its calls and their arguments, its SQL pairs, tables and charts, its retries and its
 * refusals (ADR 0012 as amended, issue #90). The store folds it here, once, and through the same
 * fold the live stream goes through, so the chat view receives turns of the same shape whether
 * they were streamed or reopened. `chatKey` changes on every switch: it is the signal the chat
 * view resets its live turns on.
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  retitleConversation,
} from "./api";
import type { Thread } from "./api";
import { replayTurns } from "./trace";
import type { Turn } from "./trace";

const LIST_FAILURE = "Could not load your conversations.";
const OPEN_FAILURE = "Could not open that conversation.";
const DELETE_FAILURE = "Could not delete that conversation.";
const TITLE_FAILURE = "the conversation title was not refreshed";

export interface ConversationsStore {
  threads: Thread[];
  activeId: string | null;
  /** The open thread's past turns, as the server still remembers them. */
  replay: Turn[];
  /** Bumped on every thread switch; the chat view drops its live turns when it changes. */
  chatKey: number;
  loading: boolean;
  error: string | null;
  newChat: () => void;
  select: (threadId: string) => void;
  remove: (threadId: string) => void;
  /** Registers the thread the first question of a draft belongs to and returns its id. */
  startThread: (title: string) => Promise<string>;
  /** Has the server title the thread from the turn it just had, and adopts the row it stores. */
  titleThread: (threadId: string) => void;
}

interface Open {
  activeId: string | null;
  replay: Turn[];
  chatKey: number;
}

const DRAFT: Open = { activeId: null, replay: [], chatKey: 0 };

/** The threads of whoever is signed in: loaded on mount, so a re-login lists that identity's. */
export function useConversations(): ConversationsStore {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [open, setOpen] = useState<Open>(DRAFT);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    listConversations()
      .then((list) => {
        if (live) setThreads(list);
      })
      .catch((cause) => {
        if (live) setError(reason(cause, LIST_FAILURE));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, []);

  const newChat = useCallback(() => {
    setError(null);
    setOpen((previous) => ({ ...DRAFT, chatKey: previous.chatKey + 1 }));
  }, []);

  // Reopening the thread already open is a no-op: it would drop the traces of this session's turns.
  const select = useCallback(
    (threadId: string) => {
      if (threadId === open.activeId) return;
      setError(null);
      setLoading(true);
      getConversation(threadId)
        .then((conversation) => {
          setOpen((previous) => ({
            activeId: conversation.thread_id,
            replay: replayTurns(conversation.messages, conversation.turns),
            chatKey: previous.chatKey + 1,
          }));
        })
        .catch((cause) => setError(reason(cause, OPEN_FAILURE)))
        .finally(() => setLoading(false));
    },
    [open.activeId],
  );

  const remove = useCallback((threadId: string) => {
    setError(null);
    deleteConversation(threadId)
      .then(() => {
        setThreads((previous) => previous.filter((thread) => thread.thread_id !== threadId));
        setOpen((previous) =>
          previous.activeId === threadId
            ? { ...DRAFT, chatKey: previous.chatKey + 1 }
            : previous,
        );
      })
      .catch((cause) => setError(reason(cause, DELETE_FAILURE)));
  }, []);

  const startThread = useCallback(async (title: string) => {
    const thread = await createConversation(title);
    setThreads((previous) => [thread, ...previous]);
    setOpen((previous) => ({ ...previous, activeId: thread.thread_id }));
    return thread.thread_id;
  }, []);

  const titleThread = useCallback((threadId: string) => {
    retitleConversation(threadId)
      .then((titled) =>
        setThreads((previous) =>
          previous.map((thread) => (thread.thread_id === titled.thread_id ? titled : thread)),
        ),
      )
      .catch((cause) => console.warn(`secure-rls: ${reason(cause, TITLE_FAILURE)}`));
  }, []);

  return {
    threads,
    activeId: open.activeId,
    replay: open.replay,
    chatKey: open.chatKey,
    loading,
    error,
    newChat,
    select,
    remove,
    startThread,
    titleThread,
  };
}

function reason(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}
