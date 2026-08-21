/**
 * The chat view: the demo's core screen. It owns one thread's turns and the stream, and
 * composes the chat bricks for everything visible. Which thread is open is owned above it
 * by `lib/conversations.ts`, so the sidebar and this view can never disagree about it.
 *
 * One turn: the question goes into the transcript, `POST /chat` opens, and every trace
 * event folds into that turn as it arrives (`lib/sse.ts` frames, `lib/trace.ts` folds).
 * A draft thread is registered lazily: the first question titles it through `onStart`.
 *
 * `replay` is what the server remembers of a reopened thread - the exchanges only. Those
 * render as plain bubbles with no trace panel: the trace is the transport of the turn that
 * produced it and is never re-served (ADR 0012), so only turns streamed in this session
 * carry one. Switching threads (a new `chatKey`) drops the live turns with it.
 *
 * A turn that never reaches an answer is shown as failed, never dressed up as one. The reason
 * is the backend's whenever the backend has one: a `done` frame with status `failed` carries the
 * server's own diagnosis, which is what the reader sees. The strings below cover only what the
 * backend cannot say - a stream that stopped without any terminal frame, and a request the API
 * refused before the turn began.
 *
 * `.chat-log` is the view's only scroll container and this view follows its bottom only
 * while the reader is already there: a token arrives many times a second and each one can
 * relayout the transcript, so yanking a reader who scrolled up would make the answer
 * unreadable. Sending is the one explicit "take me back down" and re-pins it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatMessage, Composer, ModelPicker, TracePanel } from "../components/chat";
import { EmptyState, Page, PageHeader } from "../components/layout";
import { Pill } from "../components/Pill";
import { ApiError, listModels, openChatStream } from "../lib/api";
import type { Message } from "../lib/api";
import { readTraceEvents } from "../lib/sse";
import { applyEvent, failTurn, startTurn } from "../lib/trace";
import type { Turn } from "../lib/trace";

const STREAM_CUT = "The stream ended before the turn finished.";
const GENERIC_FAILURE = "The turn failed. Try again.";
/** How far off the bottom still counts as "at the bottom": a rounding-error gap, not a scroll. */
const BOTTOM_SLACK = 24;
const PHASE_PILL = {
  gave_up: { tone: "warn", label: "gave up after retries" },
  blocked: { tone: "danger", label: "blocked by a security layer" },
  failed: { tone: "danger", label: "failed before answering" },
} as const;

/** The phases that earn a pill of their own; the rest are carried by the answer alone. */
type PilledPhase = keyof typeof PHASE_PILL;

export function ChatView({
  threadId,
  replay,
  chatKey,
  onStart,
}: {
  threadId: string | null;
  replay: Message[];
  chatKey: number;
  onStart: (title: string) => Promise<string>;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const openKey = useRef(chatKey);
  const following = useRef(true);
  const queued = useRef(false);

  // One scroll per animation frame, however many tokens landed in it.
  const follow = useCallback(() => {
    if (!following.current || queued.current) return;
    queued.current = true;
    requestAnimationFrame(() => {
      queued.current = false;
      const el = log.current;
      // Re-checked: the reader can scroll away between scheduling this frame and running it.
      if (el && following.current) el.scrollTop = el.scrollHeight;
    });
  }, []);

  useEffect(() => {
    let live = true;
    listModels()
      .then((list) => {
        if (!live) return;
        setModels(list.models);
        setModel(list.default);
      })
      .catch(() => {
        // The endpoint is unreachable: the picker says so and the turn takes the server default.
        if (live) setModels([]);
      });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    openKey.current = chatKey;
    following.current = true;
    setTurns([]);
    setStreaming(false);
  }, [chatKey]);

  useEffect(follow, [turns, replay, follow]);

  async function send(message: string): Promise<void> {
    const key = chatKey;
    const mine = () => openKey.current === key;
    // Asking is the explicit request to come back down, whatever the reader was reading.
    following.current = true;
    setTurns((previous) => [...previous, startTurn(message)]);
    setStreaming(true);
    try {
      const thread = threadId ?? (await onStart(message));
      const response = await openChatStream({
        thread_id: thread,
        message,
        model: model || undefined,
      });
      for await (const event of readTraceEvents(response)) {
        if (!mine()) return;
        setTurns((previous) => updateLast(previous, (turn) => applyEvent(turn, event)));
      }
      if (!mine()) return;
      setTurns((previous) =>
        updateLast(previous, (turn) =>
          turn.phase === "streaming" ? failTurn(turn, STREAM_CUT) : turn,
        ),
      );
    } catch (error) {
      if (!mine()) return;
      const reason = error instanceof ApiError ? error.message : GENERIC_FAILURE;
      setTurns((previous) => updateLast(previous, (turn) => failTurn(turn, reason)));
    } finally {
      if (mine()) setStreaming(false);
    }
  }

  return (
    <Page className="chat">
      <PageHeader
        eyebrow="secure-rls"
        title="Conversational data analyst"
        subtitle="Ask about your tenant's HR data. Row-level security is enforced server-side in four layers, and every step the agent takes is in the trace below its answer."
        actions={
          <div className="chat-toolbar">
            <ModelPicker
              models={models}
              value={model}
              onChange={setModel}
              disabled={streaming}
            />
          </div>
        }
      />

      <div
        ref={log}
        className="chat-log"
        onScroll={(event) => {
          const el = event.currentTarget;
          following.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
        }}
      >
        {turns.length === 0 && replay.length === 0 ? (
          <EmptyState icon="message-circle">
            Ask a question to start. Try an aggregate ("average salary per department"), a
            chart ("plot headcount by department"), or a note search.
          </EmptyState>
        ) : null}
        {replay.length > 0 ? (
          <p className="chat-replay-note">
            Replayed from the conversation the server remembers. The live trace of a past turn
            is not stored, so only turns asked in this session carry one.
          </p>
        ) : null}
        {replay.map((message, index) => (
          <ChatMessage
            key={`replay-${index}`}
            role={message.role === "user" ? "user" : "assistant"}
            text={message.content}
          />
        ))}
        {turns.map((turn, index) => (
          <TurnView key={index} turn={turn} live={streaming && index === turns.length - 1} />
        ))}
      </div>

      <Composer onSend={(message) => void send(message)} disabled={streaming} />
    </Page>
  );
}

function TurnView({ turn, live }: { turn: Turn; live: boolean }) {
  const phase = turn.phase in PHASE_PILL ? PHASE_PILL[turn.phase as PilledPhase] : null;
  return (
    <div className="chat-turn">
      <ChatMessage role="user" text={turn.question} />
      <ChatMessage
        role="assistant"
        text={turn.answer || undefined}
        footer={
          phase || turn.model ? (
            <>
              {phase ? <Pill tone={phase.tone}>{phase.label}</Pill> : null}
              {turn.model ? (
                <Pill tone="neutral" icon="cpu" title="The model that answered this turn">
                  {turn.model}
                </Pill>
              ) : null}
            </>
          ) : null
        }
      >
        {!turn.answer && turn.phase === "streaming" ? (
          <p className="msg-pending">thinking</p>
        ) : null}
        {turn.error ? <p className="form-error">{turn.error}</p> : null}
        <TracePanel items={turn.items} streaming={live} />
      </ChatMessage>
    </div>
  );
}

function updateLast(turns: Turn[], change: (turn: Turn) => Turn): Turn[] {
  if (turns.length === 0) return turns;
  const next = [...turns];
  next[next.length - 1] = change(next[next.length - 1]);
  return next;
}
