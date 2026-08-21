/**
 * The conversation-state brick: one owner for "which thread am I in, what threads do I
 * have, and what did they say". The sidebar and the chat view are siblings that both need
 * it, so it lives here rather than in either of them (CLAUDE.md, one concern one module).
 *
 * A thread is created lazily, when the first question titles it: the registry exposes no
 * title update, and ADR 0012 fixes the title as the first user message, so a thread posted
 * on the New chat click could only carry the placeholder title forever. New chat therefore
 * opens an empty draft and `startThread` registers it the moment there is a question.
 *
 * `replay` holds the exchanges the server remembers for the open thread - questions and
 * answers only. The trace of a past turn is not replayable by design (ADR 0012), so the
 * chat view renders replayed exchanges as plain bubbles and keeps trace panels for the
 * turns it streamed itself. `chatKey` changes on every switch: it is the signal the chat
 * view resets its live turns on.
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
} from "./api";
import type { Message, Thread } from "./api";

const LIST_FAILURE = "Could not load your conversations.";
const OPEN_FAILURE = "Could not open that conversation.";
const DELETE_FAILURE = "Could not delete that conversation.";

export interface ConversationsStore {
  threads: Thread[];
  activeId: string | null;
  replay: Message[];
  /** Bumped on every thread switch; the chat view drops its live turns when it changes. */
  chatKey: number;
  loading: boolean;
  error: string | null;
  newChat: () => void;
  select: (threadId: string) => void;
  remove: (threadId: string) => void;
  /** Registers the thread the first question of a draft belongs to and returns its id. */
  startThread: (title: string) => Promise<string>;
}

interface Open {
  activeId: string | null;
  replay: Message[];
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
            replay: conversation.messages,
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
  };
}

function reason(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback;
}
