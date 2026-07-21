import { Armchair, Droplets, Frame, Lamp, NotebookPen, Package, Tag } from "lucide-react";
import React from "react";

import { cn } from "../lib/cn";

const CATEGORY_GLYPHS = {
  storage: Package,
  lighting: Lamp,
  supplies: NotebookPen,
  comfort: Armchair,
  toiletries: Droplets,
  decor: Frame,
  other: Tag,
};

/**
 * No-photo state for listings. Category-aware so adjacent placeholder cards
 * differ (reads as designed, not broken), quiet so it never competes with
 * real photos. `draft` wraps the glyph in a gold dashed badge — "in
 * progress", not "missing" — for the owner's own unpublished drafts.
 */
export default function ListingPlaceholder({ category, glyphSize = 48, draft = false, className = "" }) {
  const Glyph = CATEGORY_GLYPHS[category] ?? Tag;
  return (
    <div
      aria-hidden="true"
      className={cn("flex h-full w-full items-center justify-center", className)}
      style={{
        background: "var(--bg-surface-2)",
        backgroundImage:
          "radial-gradient(color-mix(in srgb, var(--accent-tag) 7%, transparent) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }}
    >
      {draft ? (
        <div
          className="flex items-center justify-center rounded-2xl border-2 border-dashed p-4"
          style={{ borderColor: "color-mix(in srgb, var(--accent-box) 55%, transparent)" }}
        >
          <Glyph size={glyphSize} strokeWidth={1.5} style={{ color: "var(--accent-tag)", opacity: 0.3 }} />
        </div>
      ) : (
        <Glyph size={glyphSize} strokeWidth={1.5} style={{ color: "var(--accent-tag)", opacity: 0.35 }} />
      )}
    </div>
  );
}
