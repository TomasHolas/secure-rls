/**
 * SelectField — the labelled dropdown, `TextField`'s counterpart for a value that comes from a
 * fixed set. It exists because a filter must not let a reader type a department the tenant does
 * not have: the options are whatever the server listed, and the empty option is "no filter".
 *
 * The control is a native `<select>` on the shared `.select` metrics, in the `.field` + label
 * pattern `TextField` owns, so a filter row mixes the two without restyling. `chat/ModelPicker`
 * is the same element with its chrome stripped, because the prompt bar around it owns the border.
 */

export interface Option {
  value: string;
  label: string;
}

export function SelectField({
  id,
  label,
  value,
  options,
  onChange,
  placeholder,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="select"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {placeholder === undefined ? null : <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}
