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
 */

export function TextField({
  id,
  label,
  value,
  onChange,
  type = "text",
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
  autoComplete?: string;
  autoFocus?: boolean;
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        disabled={disabled}
        placeholder={placeholder}
      />
    </div>
  );
}
