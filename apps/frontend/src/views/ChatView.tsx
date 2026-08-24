/**
 * The chat view: the demo's core screen. It owns one thread's turns and the stream, and
 * composes the chat bricks for everything visible. Which thread is open is owned above it
 * by `lib/conversations.ts`, so the sidebar and this view can never disagree about it.
 *
 * One turn: the question goes into the transcript, `POST /chat` opens, and every trace
 * event folds into that turn as it arrives (`lib/sse.ts` frames, `lib/trace.ts` folds).
 * A draft thread is registered lazily: the first question titles it through `onStart`.
 *
 * `onTitled` fires once that first turn is over, whatever it ended as, and is what asks the
 * server for the generated label (ADR 0012 as amended). It runs after the stream, never during
 * it: titling is an LLM call, and one that hangs must not be able to hold up a token or the
 * turn's terminal frame. Whether the turn answered, was blocked or failed does not change it -
 * the label describes the conversation, and the server falls back to the first message.
 *
 * `replay` is what the server remembers of a reopened thread, already folded into turns by the
 * store: the questions, the answers, and the whole trace each turn produced - its reasoning, its
 * calls with the arguments the model wrote, its SQL pairs, tables and charts, its retries and its
 * refusals (ADR 0012 as amended, issue #90). A replayed turn goes through the same `TurnView` a
 * live one does, so there is one renderer and a reopened chart is the same brick as a fresh one.
 * The one thing it cannot show is how long a thought took, which this browser measured rather than
 * received. A turn whose history the caps trimmed says so on a pill of its own, and so does one the
 * server kept no history for at all - a replayed turn must never read as complete when it is not.
 * Switching threads (a new `chatKey`) drops the live turns with it.
 *
 * A turn reads top down in the order it happened: the trace of the steps first, then the answer
 * they produced, then what the turn cost beside the model that answered it. The reasoning inside
 * a step is the model's own, streamed live and collapsed until the reader asks for it.
 *
 * The prompt-guardrail position is stated twice on purpose (ADR 0011 as amended): once in the
 * header from `/health`, so the mode is visible before a question is asked, and once per finished
 * turn from its own `done` frame, which is the authoritative record. Off is loud, because a
 * refusal in that mode is a server-side layer holding rather than the model declining to try,
 * and a viewer must never have to take a demo's word for which prompt produced a trace.
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
import { ApiError, getHealth, listModels, openChatStream } from "../lib/api";
import { readTraceEvents } from "../lib/sse";
import { formatCount } from "../lib/format";
import { applyEvent, failTurn, startTurn, tokensPerSecond } from "../lib/trace";
import type { Turn, TurnUsage } from "../lib/trace";

const STREAM_CUT = "The stream ended before the turn finished.";
const GENERIC_FAILURE = "The turn failed. Try again.";
/** A generation rate reads as a speed, not a measurement: one decimal is all it carries. */
const RATE_DECIMALS = 1;
/** How far off the bottom still counts as "at the bottom": a rounding-error gap, not a scroll. */
const BOTTOM_SLACK = 24;
const PHASE_PILL = {
  gave_up: { tone: "warn", label: "gave up after retries" },
  cut_short: { tone: "warn", label: "stopped at its turn limit" },
  blocked: { tone: "danger", label: "blocked by a security layer" },
  failed: { tone: "danger", label: "failed before answering" },
  /**
   * A turn the server kept no terminal frame for - older than the retention ceiling, or from
   * before turn history existed. Without this it renders like a turn that had nothing to show,
   * which is a replayed turn looking complete when it is not.
   */
  replayed: {
    tone: "warn",
    label: "history not kept",
    title:
      "The server no longer holds this turn's trace: it is older than the retained-turns ceiling, or it predates turn history. What is shown is the transcript the conversation memory still has.",
  },
} as const;
const UNGROUNDED_LABEL = "answered without querying the data";
/** What a turn whose history the server's caps trimmed says about itself, rather than reading whole. */
const CUT_NOUN = "step";
const CUT_TITLE =
  "The server's per-turn history caps refused this many pieces of the turn, so what is replayed above is part of it and not all of it.";
const UNGROUNDED_TITLE =
  "No tool of this turn returned a result the answer could rest on, so any figure in it was not read from the database.";
/**
 * The prompt-guardrail switch as a reader sees it (ADR 0011 as amended). Off is the demo mode, so
 * it is loud: the model was not asked to refuse instructions that arrive as data, which means a
 * refusal in the trace below is a server-side layer rather than the model declining to try.
 * Row-level security is identical either way - the switch changes prompt text and reaches no layer.
 */
const GUARDRAIL_PILL = {
  on: {
    tone: "neutral",
    label: "prompt guardrails on",
    title:
      "The system prompt asks the model to refuse instructions that arrive as data and states its tenant scope. Answer-quality guidance, never a security boundary.",
  },
  off: {
    tone: "danger",
    label: "prompt guardrails off",
    title:
      "Those prompt rules are omitted, so the model attempts attacks it would otherwise decline. Row-level security is unchanged: the server-side layers are what refuses them.",
  },
} as const;

/** The phases that earn a pill of their own; the rest are carried by the answer alone. */
type PilledPhase = keyof typeof PHASE_PILL;

export function ChatView({
  threadId,
  replay,
  chatKey,
  onStart,
  onTitled,
}: {
  threadId: string | null;
  replay: Turn[];
  chatKey: number;
  onStart: (title: string) => Promise<string>;
  onTitled: (threadId: string) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [guardrails, setGuardrails] = useState<boolean | null>(null);
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
    let live = true;
    getHealth()
      .then((health) => {
        if (live) setGuardrails(health.prompt_guardrails);
      })
      .catch(() => {
        // The server did not answer, so no position is known and none is claimed.
        if (live) setGuardrails(null);
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
    // Set only for a thread registered by this send: its first turn is the one that titles it.
    let drafted: string | null = null;
    // Asking is the explicit request to come back down, whatever the reader was reading.
    following.current = true;
    setTurns((previous) => [...previous, startTurn(message)]);
    setStreaming(true);
    try {
      const thread = threadId ?? (await onStart(message));
      if (threadId === null) drafted = thread;
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
      // Off the stream on purpose, and independent of the thread the reader moved to since.
      if (drafted) onTitled(drafted);
    }
  }

  return (
    <Page className="chat">
      <PageHeader
        eyebrow="secure-rls"
        title="Conversational data analyst"
        subtitle="Ask about your tenant's HR data. Row-level security is enforced server-side in five layers, and every step the agent takes is in the trace above its answer."
        actions={
          <div className="chat-toolbar">
            <GuardrailPill state={guardrails} />
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
            Replayed from the conversation the server remembers: what each turn thought, every
            tool call with the arguments the model wrote, what ran, what came back, and anything
            retried or refused. Only how long a thought took is missing - that was measured in
            this browser rather than sent.
          </p>
        ) : null}
        {replay.map((turn, index) => (
          <TurnView key={`replay-${index}`} turn={turn} live={false} replayed />
        ))}
        {turns.map((turn, index) => (
          <TurnView key={index} turn={turn} live={streaming && index === turns.length - 1} />
        ))}
      </div>

      <Composer onSend={(message) => void send(message)} disabled={streaming} />
    </Page>
  );
}

/** One prompt-guardrail position as a pill; an unknown position draws nothing rather than guessing. */
function GuardrailPill({ state }: { state: boolean | null }) {
  if (state === null) return null;
  const pill = state ? GUARDRAIL_PILL.on : GUARDRAIL_PILL.off;
  return (
    <Pill tone={pill.tone} title={pill.title}>
      {pill.label}
    </Pill>
  );
}

/**
 * One turn, whether it streamed here or was read back: the same bricks either way. `replayed`
 * changes nothing about what is rendered - only that a reopened thread's trace starts expanded,
 * because the history is the reason the reader reopened it.
 */
function TurnView({
  turn,
  live,
  replayed = false,
}: {
  turn: Turn;
  live: boolean;
  replayed?: boolean;
}) {
  const phase = turn.phase in PHASE_PILL ? PHASE_PILL[turn.phase as PilledPhase] : null;
  // A turn that ended some other way already says so on its own pill; this one is for an answer.
  const ungrounded = turn.grounded === false && turn.phase === "ok";
  return (
    <div className="chat-turn">
      <ChatMessage role="user" text={turn.question} />
      <ChatMessage
        role="assistant"
        text={turn.answer || undefined}
        lead={<TracePanel items={turn.items} streaming={live} open={live || replayed} />}
        footer={
          phase || ungrounded || turn.model || turn.guardrails !== null || turn.cut > 0 ? (
            <>
              {phase ? (
                <Pill tone={phase.tone} title={"title" in phase ? phase.title : undefined}>
                  {phase.label}
                </Pill>
              ) : null}
              {ungrounded ? (
                <Pill tone="warn" title={UNGROUNDED_TITLE}>
                  {UNGROUNDED_LABEL}
                </Pill>
              ) : null}
              {turn.cut > 0 ? (
                <Pill tone="warn" title={CUT_TITLE}>
                  {formatCount(turn.cut, CUT_NOUN)} not stored
                </Pill>
              ) : null}
              {turn.model ? (
                <Pill tone="neutral" icon="cpu" title="The model that answered this turn">
                  {turn.model}
                </Pill>
              ) : null}
              <GuardrailPill state={turn.guardrails} />
              <TurnCost usage={turn.usage} />
            </>
          ) : null
        }
      >
        {!turn.answer && turn.phase === "streaming" ? (
          <p className="msg-pending">thinking</p>
        ) : null}
        {turn.error ? <p className="form-error">{turn.error}</p> : null}
      </ChatMessage>
    </div>
  );
}

/**
 * What the turn cost, beside the model that spent it: prompt tokens in, generated tokens out,
 * and the rate the answer came out at. A turn that reported no output has nothing to state.
 */
function TurnCost({ usage }: { usage: TurnUsage | null }) {
  const rate = tokensPerSecond(usage);
  if (!usage || usage.outputTokens <= 0) return null;
  return (
    <>
      <Pill tone="neutral" title="Prompt tokens this turn sent to the model">
        In {usage.inputTokens}
      </Pill>
      <Pill tone="neutral" title="Tokens the model generated this turn">
        Out {usage.outputTokens}
      </Pill>
      {rate === null ? null : (
        <Pill tone="neutral" icon="activity" title="Generated tokens per second">
          {rate.toFixed(RATE_DECIMALS)} T/S
        </Pill>
      )}
    </>
  );
}

function updateLast(turns: Turn[], change: (turn: Turn) => Turn): Turn[] {
  if (turns.length === 0) return turns;
  const next = [...turns];
  next[next.length - 1] = change(next[next.length - 1]);
  return next;
}
