import React from "react";

import { cn } from "../lib/cn";

/**
 * Deterministic initials avatar. The hue is hashed from the name but
 * constrained to the brand range (navy 220° → plum → maroon 330°), so every
 * avatar is on-palette while people stay distinguishable.
 */
export default function Avatar({ name, size = 40, className = "" }) {
  const cleaned = (name || "").trim();
  const initials =
    cleaned
      .split(/\s+/)
      .map((word) => word[0])
      .slice(0, 2)
      .join("")
      .toUpperCase() || "?";

  let hash = 0;
  for (let i = 0; i < cleaned.length; i += 1) {
    hash = (hash * 31 + cleaned.charCodeAt(i)) >>> 0;
  }
  const hue = 220 + (hash % 111);

  return (
    <div
      aria-hidden="true"
      className={cn("flex shrink-0 select-none items-center justify-center rounded-full", className)}
      style={{
        width: size,
        height: size,
        background: `hsl(${hue} 48% 42%)`,
        color: "white",
        fontSize: Math.round(size * 0.36),
        fontWeight: 600,
        letterSpacing: "0.02em",
      }}
    >
      {initials}
    </div>
  );
}
