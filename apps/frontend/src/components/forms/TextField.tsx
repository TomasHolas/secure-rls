/**
 * TextField — the one labelled text input brick (KB's `.field` + `.input` pattern,
 * which KB writes inline in its views; here it is a brick so no view re-styles an
 * input). Feed it a value and onChange; it stays uncontrolled of everything else.
 *
 * `type` covers the three the app needs: text, password, and the number a filter row asks for.
 * There is deliberately no `date`: a native date input renders its placeholder in the viewer's
 * locale (`dd.mm.yyyy` here, `mm/dd/yyyy` elsewhere) while the table cells, the executed SQL and
 * the server's own refusal all speak ISO, so a date filter is a text field carrying an ISO
 * placeholder instead (issue #115). The value stays a string whatever the type is, because the
 * server parses and refuses it - a half-typed date is not the browser's to interpret.
 *
 * `revealable` is the eye on a password: the brick owns the toggle rather than taking a control
 * a view hands it, which is what keeps the reveal from being spelled differently in two places
 * and from outliving the field - it is state here, so it resets whenever the field unmounts and
 * is written nowhere else. It applies to a password field only; on any other type there is
 * nothing to reveal and the prop draws no control.
 */

import { useState } from "react";

import { Icon } from "../Icon";

export function TextField({
  id,
  label,
  value,
  onChange,
  type = "text",
  revealable,
  autoComplete,
  autoFocus,
  disabled,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "password" | "number";
  revealable?: boolean;
  autoComplete?: string;
  autoFocus?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [revealed, setRevealed] = useState(false);
  const reveals = Boolean(revealable) && type === "password";

  const input = (
    <input
      id={id}
      className="input"
      type={reveals && revealed ? "text" : type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      autoComplete={autoComplete}
      autoFocus={autoFocus}
      disabled={disabled}
      placeholder={placeholder}
    />
  );

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {reveals ? (
        <div className="field-control">
          {input}
          <button
            type="button"
            className="btn-icon field-reveal"
            onClick={() => setRevealed((shown) => !shown)}
            aria-label={revealed ? "Hide password" : "Show password"}
            aria-pressed={revealed}
            aria-controls={id}
            disabled={disabled}
          >
            <Icon name={revealed ? "eye-off" : "eye"} size={18} />
          </button>
        </div>
      ) : (
        input
      )}
    </div>
  );
}
