import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { usePersistentDraftState } from "./usePersistentDraftState";

function DraftHarness({ draftKey = "test-draft" }) {
  const draft = usePersistentDraftState({
    key: draftKey,
    initialValue: { title: "" },
  });

  return (
    <div>
      <input
        aria-label="Title"
        value={draft.state.title}
        onChange={(event) =>
          draft.setState((current) => ({ ...current, title: event.target.value }))
        }
      />
      <p>{draft.hasRestoredDraft ? "restored" : "fresh"}</p>
      <button type="button" onClick={() => draft.clearDraft({ reset: true })}>
        Clear
      </button>
    </div>
  );
}

afterEach(() => {
  window.localStorage.clear();
});

describe("usePersistentDraftState", () => {
  it("restores a saved draft and clears it on demand", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<DraftHarness draftKey="listing-draft" />);

    await user.type(screen.getByLabelText("Title"), "Desk lamp");
    expect(window.localStorage.getItem("renest:draft:listing-draft:v1")).toContain(
      "Desk lamp",
    );

    unmount();
    render(<DraftHarness draftKey="listing-draft" />);

    expect(screen.getByLabelText("Title")).toHaveValue("Desk lamp");
    expect(screen.getByText("restored")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(screen.getByLabelText("Title")).toHaveValue("");
    expect(window.localStorage.getItem("renest:draft:listing-draft:v1")).toBeNull();
  });
});
