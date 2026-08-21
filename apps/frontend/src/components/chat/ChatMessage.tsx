/**
 * ChatMessage — one turn's bubble. KB has no chat UI at all, so the shape is its
 * `.ask-answer` answer card plus the icon + caps role header it puts above an answer:
 * the user's question is a compact tinted bubble, the assistant's answer the full-width
 * card that holds the trace, the answer and the status pills in that order.
 *
 * The assistant's answer arrives in markdown, so it goes through the `Markdown` brick
 * (sanitized GFM, as in KB's answer panel). The user's question stays plain text with
 * `white-space: pre-wrap`: it is what the person typed and is never interpreted as markup.
 * Structured output the model might describe - SQL, tables, charts - still has its own
 * brick in the trace, where it is the real thing rather than model-written markup.
 *
 * Three slots in reading order: `lead` above the text, `children` under it, `footer` last.
 * The lead is where the trace goes, so the steps that produced an answer are read before the
 * answer itself rather than found underneath it (ADR 0012 as amended).
 */

import type { ReactNode } from "react";

import { Icon } from "../Icon";
import { Markdown } from "../Markdown";

const ROLES = {
  user: { label: "You", icon: "user" },
  assistant: { label: "Analyst", icon: "bot" },
} as const;

export function ChatMessage({
  role,
  text,
  lead,
  children,
  footer,
}: {
  role: keyof typeof ROLES;
  text?: string;
  lead?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
}) {
  const { label, icon } = ROLES[role];
  return (
    <article className={`msg msg-${role}`}>
      <div className="msg-role">
        <Icon name={icon} size={15} />
        <span>{label}</span>
      </div>
      {lead}
      {text ? (
        role === "assistant" ? (
          <div className="msg-text markdown-body">
            <Markdown>{text}</Markdown>
          </div>
        ) : (
          <p className="msg-text">{text}</p>
        )
      ) : null}
      {children}
      {footer ? <div className="msg-footer">{footer}</div> : null}
    </article>
  );
}
