/**
 * ParamProbe — the reader's own query parameter, and what the server says it did with it.
 *
 * A listing ignores a parameter it does not read, which is correct and must stay: a stray
 * parameter cannot be allowed to break a page. Ignoring it in silence is the flaw (issue #107) —
 * an unchanged page is indistinguishable from a honored parameter, and that ambiguity is what a
 * skeptical reader presses on. So the server names every parameter it did not read, with the set
 * it does read, and this brick shows it.
 *
 * It used to make a second claim — that no request can name a tenant — and that claim is no
 * longer true of this surface: `tenant_id` is a real filter on the dataset listings, which are the
 * demo's control group (ADR 0014 as rewritten by issue #117). Rather than leave a control on
 * screen asserting something false, the box was repointed at the property that still holds
 * everywhere: a request gets exactly the parameters the endpoint declares, and is told about the
 * rest. Where the tenant genuinely cannot be named is the chat path — the agent's tenant comes
 * from the verified token and reaches its tools by closure, with no argument to fill — and the
 * explainer says that instead of implying it about a listing that filters by tenant.
 *
 * The input stays a raw `name=value` rather than any named control: it implies nothing, because a
 * query parameter is what an HTTP request already is, and it lets a viewer type their own probe
 * instead of watching a canned one.
 *
 * The notice is not tied to this box: it renders whatever the response reports as unread, from
 * wherever the parameter came.
 */

import { useState } from "react";

import { Button } from "./Button";
import { Icon } from "./Icon";
import { TextField } from "./forms";
import type { IgnoredParam } from "../lib/api";

const LABEL = "Extra query parameter";
const PLACEHOLDER = "role=admin";
const SEND = "Send";
const EXPLAINER =
  "Not a filter: whatever you type is appended to the next request exactly as typed, and what " +
  "you see below is what the server did with it. Try role=admin, or db_path=/etc/passwd. " +
  "tenant_id IS a filter here - these listings are the whole dataset, deliberately. The tenant " +
  "no request can choose is the agent's: it comes from your verified token and reaches its tools " +
  "by closure, so no tool argument and no injection can name one.";
const HEADING = "The server did not read every parameter this request carried:";

export function ParamProbe({
  id,
  ignored,
  onSend,
  disabled,
}: {
  id: string;
  ignored: IgnoredParam[];
  onSend: (probe: string) => void;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  return (
    <>
      <form
        className="search-row control-row"
        onSubmit={(event) => {
          event.preventDefault();
          onSend(draft);
        }}
      >
        <TextField
          id={id}
          label={LABEL}
          value={draft}
          onChange={setDraft}
          placeholder={PLACEHOLDER}
        />
        <Button variant="primary" type="submit" disabled={disabled}>
          {SEND}
        </Button>
      </form>
      <p className="data-table-note">{EXPLAINER}</p>
      {ignored.length > 0 ? (
        <div className="notice notice-warn" role="status">
          <Icon name="x" size={16} />
          <div>
            <strong>{HEADING}</strong>
            <ul className="ignored-list">
              {ignored.map((param) => (
                <li key={param.name}>
                  <code>{param.name}</code> - {param.reason}
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
