import { describe, expect, test } from "vitest";

import { asResults } from "./api";

describe("asResults", () => {
  test("passes bare arrays through", () => {
    expect(asResults([1, 2])).toEqual([1, 2]);
  });

  test("unwraps DRF paginated envelopes", () => {
    expect(asResults({ count: 2, next: null, previous: null, results: ["a", "b"] })).toEqual(["a", "b"]);
  });

  test("degrades to empty for junk shapes", () => {
    expect(asResults(undefined)).toEqual([]);
    expect(asResults(null)).toEqual([]);
    expect(asResults({ detail: "error" })).toEqual([]);
    expect(asResults({ results: "not-a-list" })).toEqual([]);
  });
});
