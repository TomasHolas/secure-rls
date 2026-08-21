/**
 * CodeBlock — a labelled monospace block with a copy control (KB's `.api-code` plus a
 * header row KB has no counterpart for). The trace shows SQL through this brick, so the
 * generated and the executed statement are rendered by the same code in the same
 * register and a viewer can lift either one out of the demo.
 *
 * `navigator.clipboard` is absent on insecure origins and in jsdom, so the control
 * hides itself rather than offering a button that cannot work.
 */

import { useState } from "react";

import { Button } from "./Button";

const COPY_RESET_MS = 1400;

export function CodeBlock({
  label,
  code,
  tone,
}: {
  label?: string;
  code: string;
  tone?: "accent";
}) {
  const [copied, setCopied] = useState(false);
  const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;

  async function copy(): Promise<void> {
    if (!clipboard) return;
    await clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), COPY_RESET_MS);
  }

  return (
    <div className={`code-block${tone ? ` code-block-${tone}` : ""}`}>
      {label || clipboard ? (
        <div className="code-block-head">
          {label ? <span className="code-block-label">{label}</span> : null}
          {clipboard ? (
            <Button className="btn-xs" onClick={() => void copy()}>
              {copied ? "copied" : "copy"}
            </Button>
          ) : null}
        </div>
      ) : null}
      <pre className="code-block-body">{code}</pre>
    </div>
  );
}
