import { describe, expect, test } from "vitest";

import {
  agentAttemptResolvePath,
  bandFor,
  formatMs,
  formatPercent,
  humanCode,
  needsResolution,
  thresholdsFor,
} from "./risk";

describe("risk helpers", () => {
  test("threshold curve matches the backend and keeps a 30 point review band", () => {
    expect(thresholdsFor(0)).toEqual({ review: 60, block: 90 });
    expect(thresholdsFor(50)).toEqual({ review: 40, block: 70 });
    expect(thresholdsFor(100)).toEqual({ review: 20, block: 50 });
    expect(thresholdsFor(250)).toEqual({ review: 20, block: 50 });
    for (let s = 0; s <= 100; s += 1) {
      const t = thresholdsFor(s);
      expect(t.block - t.review).toBe(30);
    }
  });

  test("bands and formatting", () => {
    expect(bandFor(null)).toBe("unknown");
    expect(bandFor(29)).toBe("low");
    expect(bandFor(30)).toBe("medium");
    expect(bandFor(60)).toBe("high");
    expect(formatPercent(0.0714)).toBe("7.1%");
    expect(formatPercent(null)).toBe("n/a");
    expect(formatMs("0.031")).toBe("0.031 ms");
    expect(formatMs(12.34)).toBe("12.3 ms");
    expect(formatMs(180.5)).toBe("181 ms");
    expect(formatMs(undefined)).toBe("n/a");
    expect(humanCode("amount_vs_history")).toBe("Amount Vs History");
  });

  test("agent attempt resolution targets the attempt under its agent", () => {
    expect(agentAttemptResolvePath("agent_01ABC", 21)).toBe("/risk/agents/agent_01ABC/transactions/21/resolve");
    expect(agentAttemptResolvePath("agent/odd id", "7")).toBe("/risk/agents/agent%2Fodd%20id/transactions/7/resolve");
    expect(needsResolution({ decision: "review" })).toBe(true);
    expect(needsResolution({ decision: "allow" })).toBe(false);
    expect(needsResolution({ decision: "block" })).toBe(false);
  });
});
