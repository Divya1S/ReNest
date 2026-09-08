import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

/** Mirrors ListingFormPage: `serialize` is an inline arrow, so its identity
 *  changes on every render. */
function UnstableSerializeHarness() {
  const renders = React.useRef(0);
  renders.current += 1;
  const draft = usePersistentDraftState({
    key: "unstable-draft",
    initialValue: { title: "", note: "" },
    serialize: (value) => ({ ...value, note: "" }),
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
      <p data-testid="renders">{renders.current}</p>
    </div>
  );
}

describe("usePersistentDraftState", () => {
  it("does not re-render in a loop when serialize is an inline function", async () => {
    // Regression: the autosave effect depended on the freshly-built serialized
    // object and wrote a new lastSavedAt each run, so it re-triggered itself
    // indefinitely: a localStorage write storm in the browser and a 5s test
    // timeout in CI.
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const user = userEvent.setup();
    render(<UnstableSerializeHarness />);

    await user.type(screen.getByLabelText("Title"), "Lamp");

    // Four keystrokes: a bounded number of writes, not hundreds.
    expect(setItem.mock.calls.length).toBeLessThanOrEqual(8);
    const renders = Number(screen.getByTestId("renders").textContent);
    expect(renders).toBeLessThan(20);
    setItem.mockRestore();
  });

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
