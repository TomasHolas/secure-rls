/**
 * Composer — the question box (KB's `.bm-composer`: a textarea over an action row).
 * Enter sends, Shift+Enter starts a line, and the whole control is disabled while a turn
 * streams so a second turn cannot race the first on the same thread.
 */

import { useState } from "react";
import type { KeyboardEvent } from "react";

import { Button } from "../Button";
import { Icon } from "../Icon";

export function Composer({
  onSend,
  disabled = false,
  placeholder = "Ask about your tenant's HR data",
}: {
  onSend: (message: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const message = draft.trim();

  function send(): void {
    if (disabled || !message) return;
    setDraft("");
    onSend(message);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    send();
  }

  return (
    <div className="composer">
      <label className="composer-label" htmlFor="chat-question">
        Question
      </label>
      <textarea
        id="chat-question"
        className="composer-input"
        rows={2}
        value={draft}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="composer-actions">
        <span className="composer-hint">Enter sends, Shift+Enter starts a new line.</span>
        <Button variant="primary" onClick={send} disabled={disabled || !message}>
          <Icon name="send" size={16} /> Ask
        </Button>
      </div>
    </div>
  );
}
