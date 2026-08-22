/**
 * ParamProbe fixtures: what the reader sends, and that the server's report is shown verbatim.
 *
 * The wording is the deliverable here (issue #107), so the assertions are about the sentence a
 * reader reads: the parameter is named, the server's own reason is repeated rather than
 * paraphrased, and nothing at all is claimed when nothing was ignored.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ParamProbe } from "./ParamProbe";

afterEach(cleanup);

const TENANT_REASON =
  "the tenant is not a parameter of this request: it is read from your verified token and " +
  "bound into the query server-side (ADR 0002, layer 1), so no request can name one";
const IGNORED = [
  { name: "tenant_id", reason: TENANT_REASON },
  { name: "db_path", reason: "not a parameter this listing reads; it reads name, sort" },
];

describe("ParamProbe", () => {
  it("names every parameter the server did not read, with the server's own reason", () => {
    const view = render(<ParamProbe id="probe" ignored={IGNORED} onSend={vi.fn()} />);

    const notice = view.container.querySelector(".notice-warn");
    expect(notice?.textContent).toContain("did not read every parameter");
    expect(notice?.textContent).toContain("tenant_id");
    expect(notice?.textContent).toContain(TENANT_REASON);
    expect(notice?.textContent).toContain("db_path");
    expect(view.container.querySelectorAll(".ignored-list li")).toHaveLength(2);
  });

  it("says nothing when the server read every parameter it was sent", () => {
    const view = render(<ParamProbe id="probe" ignored={[]} onSend={vi.fn()} />);

    expect(view.container.querySelector(".notice")).toBeNull();
  });

  it("sends what the reader typed, unaltered", () => {
    const onSend = vi.fn();
    render(<ParamProbe id="probe" ignored={[]} onSend={onSend} />);

    fireEvent.change(screen.getByLabelText("Extra query parameter"), {
      target: { value: "tenant_id=beta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("tenant_id=beta");
  });

  it("offers a parameter box rather than a tenant to pick", () => {
    render(<ParamProbe id="probe" ignored={[]} onSend={vi.fn()} />);

    expect(screen.getByText(/not a filter and not a tenant picker/i)).toBeTruthy();
    expect(screen.queryByRole("combobox")).toBeNull();
  });
});
