/**
 * TextField — the one labelled text input brick (KB's `.field` + `.input` pattern,
 * which KB writes inline in its views; here it is a brick so no view re-styles an
 * input). Feed it a value and onChange; it stays uncontrolled of everything else.
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
  type?: "text" | "password";
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
