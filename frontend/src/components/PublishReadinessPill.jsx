import React from "react";

import { cn } from "../lib/cn";

const labels = {
  needs_info: "Needs Info",
  ready: "Ready to Publish",
  published: "Published",
  donation_route: "Donation Route",
};

const tones = {
  needs_info: "border-[rgba(234,124,91,0.18)] bg-[rgba(234,124,91,0.12)] text-[color:var(--color-urgent)]",
  ready: "border-[rgba(138,29,69,0.18)] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)]",
  published: "border-[rgba(17,48,47,0.18)] bg-[color:var(--color-night)] text-white",
  donation_route: "border-[rgba(30,48,74,0.16)] bg-[color:var(--color-teal-soft)] text-[color:var(--color-teal)]",
};

export default function PublishReadinessPill({ status, className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1.5 text-[0.64rem] font-semibold uppercase tracking-[0.12em] shadow-sm",
        tones[status] || "border-[color:var(--color-line)] bg-[color:var(--color-surface-2)] text-[color:var(--color-ink)]",
        className,
      )}
    >
      {labels[status] || status}
    </span>
  );
}
