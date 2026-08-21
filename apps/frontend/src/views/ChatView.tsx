/**
 * The chat view: the demo's core screen. It owns the conversation state and the stream,
 * and composes the chat bricks for everything visible.
 *
 * One turn: the question goes into the transcript, `POST /chat` opens, and every trace
 * event folds into that turn as it arrives (`lib/sse.ts` frames, `lib/trace.ts` folds).
 * The thread is created lazily - the first question titles a `POST /conversations`
 * thread, and its id stays in this view's state until the history sidebar (issue #27)
 * takes over ownership.
 *
 * A stream that ends without a `done` event, or a request the API refuses, is shown as a
 * failed turn: the agent contract says a broken run is the caller's to render, and this
 * view never dresses one up as an answer.
 */

import { useEffect, useRef, useState } from "react";

import { ChatMessage, Composer, ModelPicker, TracePanel } from "../components/chat";
import { EmptyState, Page, PageHeader } from "../components/layout";
import { Pill } from "../components/Pill";
import { ApiError, createConversation, listModels, openChatStream } from "../lib/api";
import { readTraceEvents } from "../lib/sse";
import { applyEvent, failTurn, startTurn } from "../lib/trace";
import type { Turn } from "../lib/trace";

const STREAM_CUT = "The stream ended before the turn finished.";
const GENERIC_FAILURE = "The turn failed. Try again.";
const PHASE_PILL = {
  gave_up: { tone: "warn", label: "gave up after retries" },
  blocked: { tone: "danger", label: "blocked by a security layer" },
} as const;

export function ChatView() {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

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
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [turns]);

  async function send(message: string): Promise<void> {
    setTurns((previous) => [...previous, startTurn(message)]);
    setStreaming(true);
    try {
      const thread = threadId ?? (await createConversation(message)).thread_id;
      if (thread !== threadId) setThreadId(thread);
      const response = await openChatStream({
        thread_id: thread,
        message,
        model: model || undefined,
      });
      for await (const event of readTraceEvents(response)) {
        setTurns((previous) => updateLast(previous, (turn) => applyEvent(turn, event)));
      }
      setTurns((previous) =>
        updateLast(previous, (turn) =>
          turn.phase === "streaming" ? failTurn(turn, STREAM_CUT) : turn,
        ),
      );
    } catch (error) {
      const reason = error instanceof ApiError ? error.message : GENERIC_FAILURE;
      setTurns((previous) => updateLast(previous, (turn) => failTurn(turn, reason)));
    } finally {
      setStreaming(false);
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

      <div className="chat-log">
        {turns.length === 0 ? (
          <EmptyState icon="message-circle">
            Ask a question to start. Try an aggregate ("average salary per department"), a
            chart ("plot headcount by department"), or a note search.
          </EmptyState>
        ) : null}
        {turns.map((turn, index) => (
          <TurnView key={index} turn={turn} live={streaming && index === turns.length - 1} />
        ))}
        <div ref={bottom} />
      </div>

      <Composer onSend={(message) => void send(message)} disabled={streaming} />
    </Page>
  );
}

function TurnView({ turn, live }: { turn: Turn; live: boolean }) {
  const phase = turn.phase === "blocked" || turn.phase === "gave_up" ? PHASE_PILL[turn.phase] : null;
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
