/**
 * ChipRow — one low-cardinality filter as a row of chips: `All` plus one chip per value, the
 * picked one pressed (issue #139, the pattern beautifului.dev's FilterTable uses on our tokens).
 *
 * It replaces `SelectField` where the option set is three tenants. A native `<select>` draws its
 * popup in the OS, which means its selected row arrives in the system accent - unstyleable, and
 * the wrong green beside ours - and it hides the whole option set behind a click for no gain when
 * every option fits on one line. The chips carry no counts: a filter that advertises its own
 * bucket sizes was the half of this pattern the owner rejected.
 *
 * It is `SelectField`'s contract otherwise: the value is the caller's, `""` is no filter, and
 * `onChange` fires with the value the reader picked - so a view swaps one for the other without
 * changing when a request goes out. `aria-pressed` carries which chip is on, the group names
 * itself off the field label, and the pressed chip is lifted rather than only tinted, so the
 * state survives greyscale and the colourblind palette.
 */

const ALL = "All";

export function ChipRow({
  id,
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const chips = ["", ...options];
  return (
    <div className="field">
      <span className="field-label" id={`${id}-label`}>
        {label}
      </span>
      <div className="chip-row" role="group" aria-labelledby={`${id}-label`}>
        {chips.map((chip) => (
          <button
            key={chip}
            type="button"
            className="chip"
            aria-pressed={chip === value}
            disabled={disabled}
            onClick={() => onChange(chip)}
          >
            {chip === "" ? ALL : chip}
          </button>
        ))}
      </div>
    </div>
  );
}
