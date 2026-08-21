/**
 * ChatMessage — one turn's bubble. KB has no chat UI at all, so the shape is its
 * `.ask-answer` answer card plus the icon + caps role header it puts above an answer:
 * the user's question is a compact tinted bubble, the assistant's answer the full-width
 * card that the trace panel and the status pills hang under.
 *
 * The answer text is rendered as plain text with `white-space: pre-wrap` (KB does the
 * same for its answers). No markdown: the model's answer is prose about numbers, and
 * anything it might mark up - SQL, tables, charts - already has its own brick in the
 * trace, where it can be trusted to be the real thing rather than model-written markup.
 */

import type { ReactNode } from "react";

import { Icon } from "../Icon";

const ROLES = {
  user: { label: "You", icon: "user" },
  assistant: { label: "Analyst", icon: "bot" },
} as const;

export function ChatMessage({
  role,
  text,
  children,
  footer,
}: {
  role: keyof typeof ROLES;
  text?: string;
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
      {text ? <p className="msg-text">{text}</p> : null}
      {children}
      {footer ? <div className="msg-footer">{footer}</div> : null}
    </article>
  );
}
