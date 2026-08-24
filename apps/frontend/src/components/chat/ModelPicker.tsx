/**
 * ModelPicker — the model selector, inside the prompt bar (ADR 0005 as amended, issue #159).
 * The options are whatever `GET /models` listed, never a hardcoded list, and the view preselects
 * the `default` the same response carried; switching mid-conversation is allowed.
 *
 * KB has no select anywhere, so the control is a native `<select>` on the metrics of KB's
 * `.cfg-input`, stripped to a quiet text-and-chevron trigger for its place in the bar: the
 * chrome is the bar's, so the trigger carries none of its own until it is hovered or focused.
 * The chevron is ours because `appearance: none` takes the platform's away. An empty list means
 * `GET /models` did not answer - an unreachable endpoint, or one serving no chat-capable model
 * at all: the picker says so instead of offering nothing, and a turn sent without a model gets
 * whichever of those two the server reports.
 */

import { Icon } from "../Icon";

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
    <span className="model-picker">
      <select
        className="select model-picker-select"
        aria-label="Model"
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
      <Icon name="chevron-down" size={14} className="model-picker-chevron" />
    </span>
  );
}
