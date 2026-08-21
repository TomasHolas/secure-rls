/**
 * ModelPicker — the model selector in the chat header (ADR 0005 as amended). The options
 * are whatever `GET /models` listed, never a hardcoded list, and the view preselects the
 * `default` the same response carried; switching mid-conversation is allowed.
 *
 * KB has no select anywhere, so the control is a native `<select>` on the metrics of KB's
 * `.cfg-input` (border-only focus, no stacked focus ring). An empty list means the
 * endpoint could not be reached: the picker says so instead of offering nothing, and the
 * turn falls back to the server-side default.
 */

export function ModelPicker({
  models,
  value,
  onChange,
  disabled = false,
}: {
  models: string[];
  value: string;
  onChange: (model: string) => void;
  disabled?: boolean;
}) {
  if (models.length === 0) {
    return <span className="model-picker-empty">model list unavailable</span>;
  }

  return (
    <label className="model-picker">
      <span className="model-picker-label">Model</span>
      <select
        className="select"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {models.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
    </label>
  );
}
