/** The message bubble: the assistant's markdown becomes elements, the user's text stays literal. */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ChatMessage } from "./ChatMessage";

afterEach(cleanup);

const ANSWER = [
  "**Engineering** leads at 91000.",
  "",
  "- Engineering: 91000",
  "- Sales: 76400",
  "",
  "Counted with `COUNT(*)`.",
].join("\n");

describe("ChatMessage", () => {
  it("renders the assistant's markdown as elements, not markers", () => {
    const { container } = render(<ChatMessage role="assistant" text={ANSWER} />);

    expect(container.querySelector("strong")?.textContent).toBe("Engineering");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("code")?.textContent).toBe("COUNT(*)");
    expect(container.textContent).not.toContain("**");
    expect(container.textContent).not.toContain("`");
  });

  it("renders a GFM table as a table", () => {
    const table = ["| dept | avg |", "| --- | --- |", "| Sales | 76400 |"].join("\n");
    const { container } = render(<ChatMessage role="assistant" text={table} />);

    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
  });

  it("never turns markup in an answer into live HTML", () => {
    const { container } = render(
      <ChatMessage role="assistant" text={"<img src=x onerror=alert(1)> done"} />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("keeps the user's question literal", () => {
    const question = "what does **bold** mean in `sql`?";
    const { container } = render(<ChatMessage role="user" text={question} />);

    expect(screen.getByText(question)).toBeTruthy();
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector("code")).toBeNull();
  });
});
