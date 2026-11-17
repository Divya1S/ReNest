import { render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

vi.mock("../sentry", () => ({ Sentry: null }));

function Broken({ message }) {
  throw new Error(message);
}

describe("ErrorBoundary", () => {
  const originalLocation = window.location;
  let reload;

  beforeEach(() => {
    sessionStorage.clear();
    reload = vi.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, reload } });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
    vi.restoreAllMocks();
  });

  it("shows the error and a retry button for an ordinary render error", () => {
    render(
      <ErrorBoundary>
        <Broken message="boom" />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once for a missing chunk, then shows the error instead of looping", () => {
    const chunkError = "Failed to fetch dynamically imported module: /assets/BrowsePage-abc12345.js";

    const first = render(
      <ErrorBoundary>
        <Broken message={chunkError} />
      </ErrorBoundary>,
    );
    expect(reload).toHaveBeenCalledTimes(1);
    first.unmount();

    // The page came back after the reload and the chunk is still missing.
    render(
      <ErrorBoundary>
        <p>Loaded shell</p>
      </ErrorBoundary>,
    ).unmount();
    render(
      <ErrorBoundary>
        <Broken message={chunkError} />
      </ErrorBoundary>,
    );

    expect(reload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
