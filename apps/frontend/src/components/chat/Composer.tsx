/**
 * Composer — the prompt bar (issue #159): one rounded bar carrying the question, the model
 * picker and the send control, in that keyboard order. The textarea starts one line tall and
 * grows with the draft — measured off `scrollHeight` — up to the `--composer-cap` ceiling the
 * stylesheet owns, past which it scrolls inside itself rather than eating the transcript.
 *
 * Enter sends, Shift+Enter starts a line, and the whole bar is disabled while a turn streams
 * so a second turn cannot race the first on the same thread. The focus ring is on the bar
 * (`focus-within`) rather than on the textarea, so the three controls read as one field.
 *
 * The hint line the old block carried is gone: the key that sends is in the placeholder, which
 * is on screen exactly while the box is empty - when a reader needs it - and costs no height,
 * and the whole contract is on the send control's tooltip. A placeholder long enough to spell
 * both keys out wraps at 900px, which would cost the bar its one line at rest.
 */

import { useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { Icon } from "../Icon";
import { ModelPicker } from "./ModelPicker";

const SEND_TITLE = "Send - Enter sends, Shift+Enter starts a new line";

export function Composer({
  onSend,
  models,
  model,
  onModelChange,
  disabled = false,
  placeholder = "Ask about your tenant's HR data - Enter sends",
}: {
  onSend: (message: string) => void;
  models: string[];
  model: string;
  onModelChange: (model: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const message = draft.trim();

  // Re-measured on every draft, so sending shrinks the bar back to one line as well.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

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
      <div className="composer-bar">
        <textarea
          ref={box}
          className="composer-input"
          rows={1}
          aria-label="Question"
          value={draft}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="composer-tools">
          <ModelPicker
            models={models}
            value={model}
            onChange={onModelChange}
            disabled={disabled}
          />
          <button
            type="button"
            className="btn-icon composer-send"
            onClick={send}
            disabled={disabled || !message}
            aria-label="Send"
            title={SEND_TITLE}
          >
            <Icon name="arrow-up" size={18} />
          </button>
        </div>
      </div>
    </div>
  );
}
